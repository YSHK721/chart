"""トレース読み取りの契約（usecase 層・Port・RUN_TRACE_BASIC_DESIGN §9.3）。

**なぜ Port が要るか（DIP・層ゲート）**: 読む相手は parquet であり、その到達点は
`simulator/adapter/trace/parquet_trace_store.py`（pandas / pyarrow の唯一の到達点）で
ある。usecase は adapter を掴めない（`simulator/sim_ui/tests/unit/test_sim_ui_import_direction.py`
と層順序ゲートが固定している）ので、契約をこちらに置いて実装を外側から差し込む。

**なぜ別ファイルか（ISP）**: 既存の `simulator/sim_ui/usecase/job_ports.py` はジョブの
生成・照会・取消という**別のアクター**の契約である。トレースの読み取りはそれらと違う
理由で変わる（列が増える・窓の単位が変わる）。先例は別ファイル Port 6 本
（marker_ports / optimize_ports / scan_contacts_ports / sizing_ports /
validation_ports / vol_band_ports）。

**`count` を `read` と分けて宣言する理由**（絶対命令・§9.6）:
    「窓が広すぎるか」を読んでから判定するのは「作ってから捨てる」形である。実測
    1,036,394 行の成果物に対しては、断るために 100MB を materialise することになる。
    件数は parquet の述語評価だけで答えられる（行の materialise は起きない）ので、
    判定用の問いを**独立した契約**として立てる。1 メソッドの Port に畳むと、
    実装は「読んで len を返す」ことができてしまい、規律が消える。

依存規律: pandas / pyarrow を import しない。列は素の並び（`list`）で受け渡す。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class TraceExtent:
    """その run のトレースがどこからどこまで・何行あるか、と run の設定値。

    front が窓を選ぶために要る（窓を選ぶために全行を読ませない）。

    ``rows``: 記録行数。
    ``first_time`` / ``last_time``: 記録の下端・上端（epoch **ミリ秒**・§9.0）。
        行 0 件のときは両方 `None`（「その期間に評価点が無かった」を値で読めるように）。
    ``initial_deposit``: DD の基準（`usecase/mt5_parity` の式が要る B_0）。
    ``margin_level_floor``: 維持率の閾値。**run の設定（stop-out 水準）**であり、
        分析側が発明する値ではない。設定が無い run は `None`（閾値が無ければ
        「割れ」は定義できない）。
    """

    rows: int
    first_time: "int | None"
    last_time: "int | None"
    initial_deposit: float
    margin_level_floor: "float | None"
    #: 最初に問うべき窓 `[start, end)`（epoch ミリ秒）。記録が無い run は `(None, None)`。
    #:
    #: **これを Port の DTO に持たせず usecase が埋める**（query_trace が返す量の
    #: 決定者だから）。既定を `(None, None)` にしてあるのは、Port の実装（adapter）が
    #: 返す量の上限を知らないためである——知っていたら、上限の 2 つ目の所有者ができる。
    suggested_window: "tuple[Any, Any]" = (None, None)


class TracePointsPort(abc.ABC):
    """点粒度トレースの読み取り契約。

    **例外契約（全メソッド共通・LSP）**: 実装は以下の 4 種**だけ**を送出してよい。
    呼出側（`simulator/sim_ui/usecase/query_trace.py`）はこれを握らず素通しし、
    HTTP の状態への翻訳は `simulator/sim_ui/adapter/trace_api_controller.py` の
    _guarded ただ 1 箇所が持つ。

        `TraceArtefactMissingError`   分析の前提が存在しない（下の docstring を読む）
        ResultNotAvailableError       ジョブが未完了（部分結果は公開しない）
        JobNotFoundError              そのジョブが台帳に無い
        ValueError                    受理形でない入力（識別子・ファイル名の CWE-22
                                      防御は関門から、成果物に無い列名は読み口から）

    ResultNotAvailableError / JobNotFoundError の定義元は
    `simulator/sim_ui/usecase/job_models.py` であり、実装が自分で判定するのではなく
    **公開可否の関門から素通しで上がる**（規則の実体は
    `simulator/sim_ui/usecase/fetch_job_result.py` ただ 1 つ・§9.4）。本モジュールが
    これらを import しないのは、読み取りの契約が公開可否の規則を**知らない**ためである
    （知れば関門の 2 人目の所有者になる）。

    ここへ宣言を置く理由: 基底が宣言していない例外を派生型が投げると置換可能性が壊れる
    ——呼出側は基底の契約しか見ずに書かれるため、宣言の無い例外は必ず握り漏れる。

    **本宣言は規範（実装が満たすべき形）であり、現実装が全経路で満たしている証明では
    ない**。実測（2026-09-11・型階層）: pyarrow の `ArrowInvalid` は ``ValueError`` の
    派生なので上の 4 種に収まるが、``ArrowIOError`` は ``OSError`` の派生であり収まらない。
    後者を実際に起こす経路は本環境（uid 0）では再現できず**未実証**である。実装側で
    IO 失敗を本集合へ写す是正が要るかは、再現経路を実測してから決める（申し送り）。
    """

    @abc.abstractmethod
    def extent(self, job_id: str) -> TraceExtent:
        """記録の範囲と run の設定値を返す。**行を読まない**ことが契約である。

        `suggested_window` は埋めなくてよい（既定 `(None, None)`）。どの窓なら返せるかは
        返す量の上限を知る usecase の関心であり、読み方を知る実装の関心ではない。

        例外: 上の共通契約のとおり。特に `initial_deposit`（DD の基準）を run から
            決められないときは `TraceArtefactMissingError` である——0.0 で埋めると
            `simulator/usecase/mt5_parity.py` の equity_dd_absolute（B_0 − min equity）
            が**例外も掲示も出さずに**誤った金額を返す（§7「既定値で黙って埋めない」）。
        """

    @abc.abstractmethod
    def count(
        self, job_id: str, *, start: "int | None" = None, end: "int | None" = None
    ) -> int:
        """窓 `[start, end)`（epoch ミリ秒）に入る行数を返す。**行を読まない**。

        事前条件: 実装は ``start`` / ``end`` の大小を検査してよいが、**それより強い
            事前条件を課してはならない**（窓を必須にする・上限を適用する等）。返す量の
            上限は usecase ただ 1 つが持つ——実装が知ると上限の 2 人目の所有者ができる。
        例外: 上の共通契約のとおり。
        """

    @abc.abstractmethod
    def read(
        self,
        job_id: str,
        *,
        columns: "Sequence[str]",
        start: "int | None" = None,
        end: "int | None" = None,
    ) -> "dict[str, list]":
        """窓 `[start, end)` の**指定列だけ**を「列名 → 素の list」で返す。

        事後条件: 返す並びは ``columns`` の宣言順・行は記録順。窓の外の行は
            1 つも含まない（読んでから捨てる形を実装側で作らない・§9.6）。値は素の
            `int` / `float` / `bool` であり、pandas / numpy の型を外へ出さない。
        例外: 上の共通契約のとおり。成果物に無い列名は `ValueError` である——黙って
            落とすと front が「その列は空だった」と読む（別の事実である）。
        """


class TraceArtefactMissingError(Exception):
    """分析の前提が存在しない。「0 行だった」とは**別の事実**である。

    「0 行だった」と区別できる状態を保つために例外にする。0 行で返すと front は
    「その run は何も起きなかった」と読む（別の事実）。

    該当する状態（実装の実際の送出点と一致させてある。
    `simulator/sim_ui/adapter/trace_query_source.py`）:

        * トレース成果物そのものが無い（記録 OFF で走らせた run）
        * 投入仕様が読めない・DD の基準（`initial_deposit`）が仕様に無い
          ——値を発明すれば金額が黙って誤る

    別々の例外型に割らないのは、呼出側が採る手が同じ（分析を出さず理由を掲示する・
    HTTP では 404 ＋ 文言）であり、型で分けても分岐が増えるだけだからである。
    """


__all__ = ["TraceArtefactMissingError", "TraceExtent", "TracePointsPort"]
