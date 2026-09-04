"""compute_indicators の協調子ポート群（ISSUE-182 item4: 契約未定義の解消）。

usecase（方針側＝Application Business Rules）が所有する境界ポート。
:func:`usecase.compute_indicators.compute_indicators` は 5 依存を呼出時注入で受けるが、是正前は
契約が定義されていたのは ``dataset_port``（:class:`~usecase.dataset_port.DatasetPort`）のみで、
残りは ``Any`` / ``Callable[..., list]`` / ``type`` ＝ **契約未定義**だった
（``indigators/PORTING_GUIDE.md`` の「境界は Protocol で定義する」と不整合）。usecase が
「注入される協調子は何を満たすべきか」を宣言しないため、欠落は実際に呼ばれるまで検出されない。

本モジュールは注入される具象の **実シグネチャを実測**して契約化する（推測でメソッドを増やさない）:

======================  =====================================================================
Port                    実測した注入具象（本番）
======================  =====================================================================
IndicatorComputePort    ``adapter.compute.IndicatorComputeAdapter``
FormingBarPort          ``adapter.compute.forming_bar``（module・2 メソッドのみ使用）
ComputeDispatchPort     ``adapter.compute.latest_dispatch.full_compute``
LatestComputeDispatchPort ``adapter.compute.latest_dispatch.latest_compute``
ComputeErrorPort        ``adapter.compute.ComputeError``（``compute_error`` は**型**として注入）
======================  =====================================================================

ISP: ポートは usecase が実際に使うメンバだけを宣言する。
  - :class:`FormingBarPort` は ``resolve_now_unix`` / ``apply_forming_bar`` の 2 つのみ。
    同じ ``forming_bar`` module を注入される :func:`usecase.serve_candles.serve_forming_bar` は
    ``rollup_forming_bar`` / ``is_tick_ref`` 等の別集合を使うが、それは別クライアントの契約であり
    本 Port には含めない（含めると /compute の代替実装へ不要な実装を強要する）。
  - ディスパッチは full / latest で実測シグネチャが異なる（``min_tail`` は latest のみが受ける
    キーワード専用引数）ため、単一の Port には束ねられない。2 つの Port として分ける。

DIP: 本モジュールは adapter / marketdata を import しない（Dependency Rule。``pandas`` の
``DataFrame`` 等インフラ型も持ち込まず ``Any`` で受ける＝型でのインフラ漏出を作らない）。

挙動不変: 契約は型注釈と ``tests/test_usecase_compute_ports.py`` の回帰ガードで固定する。
:func:`~usecase.compute_indicators.compute_indicators` に ``isinstance`` 強制は入れない
（実行時の分岐を増やさない＝参照実装 :class:`~usecase.dataset_port.DatasetPort` と同じ扱い）。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IndicatorComputePort(Protocol):
    """指標計算アダプタの抽象（``full_compute`` / ``latest_compute`` の第 1 引数）。

    usecase 自身は本オブジェクトのメソッドを呼ばず、ディスパッチへ素通しする。ただし
    「ディスパッチが ``compute(...)`` を呼ぶ」ことは usecase が呼出元に対して負う契約であるため、
    ``Any`` ではなく本 Port で宣言する。実測: ``IndicatorComputeAdapter.compute``。
    """

    def compute(
        self, compute_id: str, variant: str, df: Any, params: "dict[str, Any]"
    ) -> "list[dict[str, Any]]":
        """指標を計算し系列 JSON（``list[dict]``）を返す。失敗は ComputeError を送出する。"""
        ...


@runtime_checkable
class FormingBarPort(Protocol):
    """形成中バー協調子の抽象。ライブの計算窓をチャートの窓へ揃えるために全 mode が使う。"""

    def resolve_now_unix(self, override: Any = None) -> int:
        """基準時刻 now（UNIX 秒・UTC）を解決する（時刻取得の単一注入点）。"""
        ...

    def apply_forming_bar(self, df: Any, ref: str, tf: str, now_unix: int, *,
                          synthesize_closed_gaps: bool = True) -> Any:
        """``df``（date-index OHLCV）の末尾へ現在形成中バーを set/replace した DataFrame を返す。

        ``synthesize_closed_gaps``: 欠落閉周期（M1 未焼き込みの**閉じた**バー）を実 tick から
        合成して併せて注入するか。確定値の前倒し＝後で M1 に上書きされるため足内更新
        （``mode="latest"``）専用で、full は ``False``（ISSUE-361）。
        """
        ...


@runtime_checkable
class ComputeDispatchPort(Protocol):
    """全件計算ディスパッチ（``mode="full"``）の抽象。実測: ``latest_dispatch.full_compute``。"""

    def __call__(
        self,
        adapter: IndicatorComputePort,
        compute_id: str,
        variant: str,
        df: Any,
        params: "dict[str, Any]",
    ) -> "list[dict[str, Any]]":
        """全件で ``adapter.compute(...)`` を呼び系列 JSON を返す。"""
        ...


@runtime_checkable
class LatestComputeDispatchPort(Protocol):
    """Latest（末尾 K）計算ディスパッチの抽象。実測: ``latest_dispatch.latest_compute``。

    ``min_tail``（ISSUE-162・additive なキーワード専用引数）を受ける点だけが
    :class:`ComputeDispatchPort` と異なるため、別 Port として宣言する。
    """

    def __call__(
        self,
        adapter: IndicatorComputePort,
        compute_id: str,
        variant: str,
        df: Any,
        params: "dict[str, Any]",
        *,
        min_tail: "int | None" = None,
    ) -> "list[dict[str, Any]]":
        """``min_window`` で tail した df を計算し、系列 data を末尾 K 点へ切って返す。"""
        ...


@runtime_checkable
class PeriodBoundaryPort(Protocol):
    """時刻が属する期間の始端を解決する抽象（ISSUE-274）。実測: ``forming_bar.period_start_unix``。

    ISP: 上位足投影は「その時刻がどの H 期間に属するか」だけを要し、形成中バーの合成
    （:class:`FormingBarPort`）は要さない。暦足ではラベル ≠ 期間始端 ≠ 確定時刻であり、
    ラベルによる大小比較は未来情報の混入を生むため、判定は必ず本ポートを通す。
    """

    def period_start_unix(self, now_unix: int, tf: str) -> int:
        """``now_unix`` が属する ``tf`` 期間の UTC 始端（UNIX 秒）を返す。"""
        ...


@runtime_checkable
class MtfProjectionPort(Protocol):
    """上位足系列をチャート足の時間軸へ投影する抽象。実測: ``mtf_projection.project_series``。"""

    def __call__(
        self,
        series: "list[dict[str, Any]]",
        df_chart: Any,
        compute_tf: str,
        *,
        period_start_unix: Any,
    ) -> "list[dict[str, Any]]":
        """``series``（H の時間軸）を ``df_chart`` の各バー時刻へ前方保持で写して返す。"""
        ...


@runtime_checkable
class ComputeErrorPort(Protocol):
    """指標計算が送出する例外の抽象（``error_type`` / ``message`` を持つ）。

    ``compute_indicators`` は本 Port を満たす例外の**型**を ``compute_error`` として受け取り、
    ``except`` 節に用いたうえで 2 属性を Output Model（``ComputeResult``）へ載せる
    （error_type → HTTP ステータスの対応は presentation の責務のため本層は持たない）。
    実測: ``adapter.compute.ComputeError``（``@dataclass`` ＋ ``Exception``）。
    """

    #: 翻訳済みのエラー種別（"validation" / "empty_series" / "missing_column" 等）。
    error_type: str
    #: 利用者向けメッセージ。
    message: str
