"""Output Boundary（P-1〜P-4 ＋ 役割宣言）。usecase はこの Protocol 越しにしか外を知らない。

YAGNI 裁定（arch-spec §2）: PriceValueMapPort / SheetPresenterPort / SheetStateStorePort /
TemplateBindingPort / HorizonPolicyPort / ReachDefinitionPort は**作らない**。

P-1 は「束契約」である（1 呼出 = 1 計算 = 3 消費者（ラダー / 第 2 表 / 価格投影）で共有）。
P-3 は§7 の計算量 Spy が数える**唯一の面**であり、前進評価はここ以外から発行されない。
"""
from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from dashboard_ui.domain.bar import Bar
from dashboard_ui.usecase.sheet_models import OscillatorSpec, SeriesRole, SheetInstance


@runtime_checkable
class IndicatorSeriesPort(Protocol):
    """P-1 既存 `/compute` の全件系列を読む（新規の計算を発行しない）。"""

    def full_series(
        self, *, indicator_id: str, variant: str, params: Mapping[str, object],
        dataset_ref: str, timeframe: str,
    ) -> "Mapping[str, tuple[tuple[int, float], ...]]":
        """系列名 → ((time, value), ...)。"""


@runtime_checkable
class BarSupplyPort(Protocol):
    """P-2 足の供給。"""

    def bars(self, *, dataset_ref: str, timeframe: str) -> "tuple[Bar, ...]":
        """供給されている足の全件（時刻昇順）。

        **末尾は形成中の足でありうる**（現在の周期に届いていれば :meth:`forming_bar` が
        返すのと**同一物**である）。「確定足の全件」ではない: そう読むと `bars()[-1]` を
        確定値として扱う実装が生まれ、形成中の足を確定値として使う無言の誤りになる。
        確定足だけを見たい呼び出し側は `bars()[-2]` を取る。
        """

    def forming_bar(
        self, *, dataset_ref: str, timeframe: str, now_unix: int
    ) -> "Bar | None":
        """形成中の足（無ければ None）。"""


class SeriesSupplyUnavailable(RuntimeError):
    """P-1 がその instance の系列を供給できない（契約上の失敗）。

    ライブ core に (indicatorId, variant) の束縛が無い等、**当該 instance に固有**の
    供給失敗を表す。実装（adapter）が投げる具象例外は本型を継承するか本型で包む。

    扱いは `ForwardEvaluationUnavailable` と同じく §5.5.1 の構造的除外である:
    シート全体を落とさず当該 instance だけを外し、除外した instance と理由を
    応答の縮退一覧（degradations）へ必ず出す（§7・無言の縮退禁止）。
    表示時間足の足が 1 本も無い等の**要求全体**の失敗は本型ではなく ValueError のまま
    （現在値が決まらない以上、返せるシートが存在しない）。
    """


class ForwardEvaluationUnavailable(RuntimeError):
    """P-3 がその instance の値を出せない（契約上の失敗）。

    実装（adapter）が投げる具象例外は本型を継承する。usecase が adapter の例外型を知らずに
    「出せない instance」を扱えるようにするための境界であり、依存方向は外→内のままである。

    扱いは §5.5.1 の**構造的除外と同じ**である（`breakpoints()` を提供できない指標が
    価格投影の対象外になるのと同型）。ただし**無言では外さない**: 除外した instance と理由は
    応答の縮退一覧（degradations）へ必ず現れる（§7）。
    """


@runtime_checkable
class ForwardEvaluationPort(Protocol):
    """P-3 前進評価 `forward(C) -> value`（既存の増分器をそのまま呼ぶ・core は無改変）。

    Raises:
        ForwardEvaluationUnavailable: その instance の値を出せないとき。
    """

    def value_at_close(
        self, *, indicator_id: str, variant: str, params: Mapping[str, object],
        dataset_ref: str, timeframe: str, close: float,
    ) -> float:
        """終値候補 `close` を置いたときの当該バーの指標値。"""


@runtime_checkable
class BreakpointSourcePort(Protocol):
    """P-4 指標側が実装する**唯一のメソッド**（ISP。逆関数の数式は要求しない）。"""

    def breakpoints(
        self, *, bar: Bar, params: Mapping[str, object], prev_value: "float | None"
    ) -> "tuple[float, ...]":
        """区分の境目（適用価格の折れ・上下分岐）。"""


@runtime_checkable
class BreakpointRegistryPort(Protocol):
    """指標 → BreakpointSource。提供できない指標は**キーが無い**（列挙で除外を書かない）。"""

    def resolve(self, indicator_id: str) -> "BreakpointSourcePort | None":
        """無ければ None（＝価格へ逆算できない＝§5.5 の対象外が構造で表れる）。"""

    def invertible_ids(self) -> "frozenset[str]":
        """§7.1 の契約テストが読む集合。"""


@runtime_checkable
class SeriesRolePort(Protocol):
    """系列の役割・表示ラベル・オシレータ宣言（判定表そのものは adapter が所有する）。

    §3.1: 水準でない系列の除外は**名前ではなく実値の桁**（現在値の 0.3〜3 倍）で判定する。
    §8 OCP: 積み上がる量かどうかも「性質の宣言」で切り替え、指標名で分岐しない。
    """

    def role_of(
        self, *, instance: SheetInstance, series_name: str,
        values: "tuple[float, ...]", reference_price: float,
    ) -> SeriesRole:
        """その系列が価格スケールの水準か否か。"""

    def row_label(self, *, instance: SheetInstance, series_name: str) -> str:
        """ラダー行のラベル（§11-2: パラメータまで含めて一意にする）。"""

    def known_params(self, *, indicator_id: str) -> "frozenset[str] | None":
        """カタログが定義するパラメータ名の集合（指標がカタログに無ければ None）。

        撤去済みパラメータの残骸（保存済みテンプレート由来）を入口で正規化除去するために
        使う。残骸を残すと、実質同一の instance が別キーに割れて行ラベルの §11-2 一意性と
        衝突する（2026-08-30 実測: `wait_for_close` だけが違う MA 2 本が 400 全滅を起こした）。
        """

    def oscillator_spec(
        self, *, instance: SheetInstance, series_names: "frozenset[str]"
    ) -> "OscillatorSpec | None":
        """第 2 表のセル宣言（オシレータでなければ None）。"""
