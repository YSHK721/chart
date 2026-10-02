"""足の成果物の読み取りの契約（usecase 層・Port・ISSUE-552/554 段階 2-1）。

**なぜ Port が要るか（DIP・層ゲート）**: 読む相手は parquet であり、その到達点は
`simulator/adapter/trace/parquet_trace_store.py` である。usecase は adapter を掴めないので、
契約をこちらに置いて実装を外側から差し込む（`simulator/sim_ui/usecase/trace_query_ports.py`
と同じ形）。

**なぜ分析の Port（「`TracePointsPort`」）と別か（ISP）**: あちらは評価点ごとの記録を時刻の窓で
読み、DD の基準や維持率の閾値を運ぶ。こちらは足ごとの列を**位置の区間**で読み、列と指標の
宣言を運ぶ。変わる理由が違う（分析の列が増える／チャートの列が増える）。

**`count` を `read` と分けて宣言する理由**: 「区間が広すぎるか」を読んでから判定するのは
「作ってから捨てる」形である。件数は行を組み立てずに答えられるので、判定用の問いを独立した
契約として立てる（1 メソッドに畳むと、実装は「読んで len を返す」ことができてしまう）。

依存規律: pandas / pyarrow を import しない。列は素の並び（`list`）で受け渡す。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ChartBarsDeclaration:
    """足の成果物が名乗る宣言（書き手が成果物の隣に書いたもの）。

    ``rows``: 足の本数。
    ``index_column``: 区間の指定に使う列（Bar 列の中の位置・0 始まり・連番）。
    ``columns``: 成果物が持つ列（宣言順）。読み手は列名を手書きしない。
    ``indicators``: 指標と列の対応（``{"series", "placement", "column"}`` の並び）。
    ``timeframe`` / ``ea_name`` / ``dataset_ref``: その run の事実。``dataset_ref`` は
        台帳に無い実体で走った run では `None`（推測しない）。
    ``time_unit``: 時刻の列の単位。
    ``stop_out_level``: その run が使ったストップアウト水準（ISSUE-546・trace_meta.json と同じ出所）。
        売買履歴チャートの証拠金維持率の面の基準（2026-10-02）。宣言に書く前に実行したジョブでは
        `None`（推測しない）。
    """

    rows: int
    index_column: str
    columns: "tuple[str, ...]"
    indicators: "tuple[Mapping[str, str], ...]"
    timeframe: str
    ea_name: str
    dataset_ref: "str | None"
    time_unit: str
    stop_out_level: "float | None" = None


class ChartBarsPort(abc.ABC):
    """足の成果物の読み取り契約。

    **例外契約（全メソッド共通・LSP）**: 実装は以下の 4 種だけを送出してよい。呼出側
    （`simulator/sim_ui/usecase/query_chart_bars.py`）は握らず素通しし、HTTP の状態への翻訳は
    adapter の出口が持つ。

        `ChartBarsArtefactMissingError`   足の成果物またはその宣言が無い・読めない
        ResultNotAvailableError           ジョブが未完了（部分結果は公開しない）
        JobNotFoundError                  そのジョブが台帳に無い
        ValueError                        受理形でない入力（識別子・成果物に無い列名）

    ResultNotAvailableError / JobNotFoundError は公開可否の関門
    （`simulator/sim_ui/usecase/fetch_job_result.py`）から素通しで上がる。本モジュールは
    公開可否の規則を知らない（知れば関門の 2 人目の所有者になる）。
    """

    @abc.abstractmethod
    def declaration(self, job_id: str) -> ChartBarsDeclaration:
        """宣言を返す。**行を読まない**ことが契約である。"""

    @abc.abstractmethod
    def count(self, job_id: str, *, start: int, end: int) -> int:
        """位置の半開区間 `[start, end)` に入る行数を返す。**行を読まない**。

        実装は返す量の上限を適用してはならない（上限は usecase ただ 1 つが持つ）。
        """

    @abc.abstractmethod
    def read(
        self, job_id: str, *, columns: "Sequence[str]", start: int, end: int
    ) -> "dict[str, list]":
        """位置の半開区間 `[start, end)` の**指定列だけ**を「列名 → 素の list」で返す。

        事後条件: 並びは ``columns`` の宣言順・行は位置の昇順。区間の外の行は 1 つも
            組み立てない。
        """


class ChartBarsArtefactMissingError(Exception):
    """足の成果物（またはその宣言）が無い・読めない。「0 行だった」とは別の事実である。

    該当する状態: 本段階より前に走らせたジョブ（足の成果物を書いていない）・書出しに
    失敗した run。0 行で返すと画面は「その run に足が無かった」と読む（別の事実）。
    """


__all__ = ["ChartBarsArtefactMissingError", "ChartBarsDeclaration", "ChartBarsPort"]
