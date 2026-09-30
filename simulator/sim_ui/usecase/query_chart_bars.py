"""売買履歴チャートへ返す足の範囲を決める（usecase 層・ISSUE-552/554 段階 2-1）。

アクター（改訂の動機）: **区間の解釈と、1 回に返す量の決定**。読み方
（`simulator/adapter/trace/parquet_trace_store.py`）とも、書き方
（`simulator/sim_ui/adapter/chart_overlay_writer.py`）とも別の理由で変わる。

**なぜ範囲で返すか（実測 2026-09-30・ISSUE-552）**: 1 分足の全履歴 run は 2,152,183 本あり、
画面が足と値を丸ごと持つとタブが落ちた。画面が持つのは表示する範囲だけにし、サーバが
位置の区間を受けて必要ぶんだけ返す。

**区間は位置（Bar 列の中の番号）で指定する**: 時刻の窓にすると、足の密度が一様でない
（週末・立会時間）ため「何本入るか」を画面が決められない。位置なら区間の幅が本数である。

**間引かない**: 絞り方は区間を狭めることただ 1 つとし、上限を超える区間は
`ChartBarsRangeTooWideError` で断る（`simulator/sim_ui/usecase/query_trace.py` と同じ方針）。

**上限判定に行を読まない**: 件数は Port の `count` に問い、読みは通ったときにしか発行しない。

依存規律: adapter を掴まない（読み取りは `ChartBarsPort` 経由）。pandas / pyarrow を import しない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from simulator.sim_ui.usecase.chart_bars_ports import ChartBarsDeclaration, ChartBarsPort

#: 1 回の問い合わせで返す足の本数の上限。
#:
#: 値の根拠（実測 2026-09-30）: 2,152,183 行 × 15 列の成果物から 20,000 行を位置の区間で
#: 読む時間は 13〜50 ms（行グループ既定・区間の場所による）。上限は「区間を狭めよ」と
#: 伝えるための関門であって間引きの発動点ではない（間引きは行わない）。分析の上限
#: （「`query_trace.MAX_RETURNED_ROWS`」）とは別の量（評価点ではなく足）なので別に宣言する。
MAX_RETURNED_BARS = 20_000

#: 位置を保持する符号つき整数の幅（bit）。足の成果物は位置の列を符号つき 64 bit 整数で持つ
#: （書き手 `simulator/sim_ui/adapter/chart_overlay_writer.py` が書いた実物の型と一致することを
#: `simulator/sim_ui/tests/integration/test_serve_sim_chart_bars.py` が固定する）。
_POSITION_BITS = 64

#: 位置の上界（含む）。これを超える位置は、どの run のどの成果物にも存在し得ない。
#:
#: run の行数を上界にしない理由: run の末尾を越えた区間は「在る行だけ返す」仕様であり、
#: 行数で断ると その仕様を狭める。ここで断るのは「位置として表せない値」だけである。
#: 実測（2026-09-30）: 上界 + 1 を読み口へ渡すと述語の組み立てで `OverflowError` になり、
#: 失敗の翻訳（400）を通らずに接続が切れた。上界ちょうどは在る行だけ返る。
MAX_POSITION = 2 ** (_POSITION_BITS - 1) - 1


class ChartBarsRangeTooWideError(Exception):
    """区間に入る足の本数が上限を超えた。黙って間引かず、上限を添えて断る。"""


@dataclass(frozen=True)
class ChartBarsRows:
    """区間 1 つぶんの答え。

    ``start`` / ``end``: 問われた位置の半開区間。
    ``rows``: 返した行数（run の末尾を越えた区間では ``end - start`` より少ない）。
    ``columns``: 宣言された列（列名 → 素の並び）。
    """

    start: int
    end: int
    rows: int
    columns: "Mapping[str, list]"


class QueryChartBarsInteractor:
    """位置の区間を解釈して、足・口座・指標の列を返す。"""

    def __init__(self, *, source: ChartBarsPort) -> None:
        self._source = source

    def extent(self, job_id: str) -> ChartBarsDeclaration:
        """行数・列・指標の宣言（行を 1 つも読まない）。"""
        return self._source.declaration(job_id)

    def rows(self, job_id: str, *, start: int, end: int) -> ChartBarsRows:
        """位置の半開区間 `[start, end)` の行を**間引かず全部**返す。

        事前条件: ``0 <= start <= end <= MAX_POSITION``。
        例外:
            `ValueError`                   — 区間の形が不正（数えも読みもしない）。
            `ChartBarsRangeTooWideError`   — 区間に入る行数が `MAX_RETURNED_BARS` を超える。
                このとき**行の読みは 1 回も発行しない**（作ってから捨てない）。
        """
        if start < 0 or end < 0 or start > end:
            raise ValueError(
                f"足の区間は 0 以上の位置で開始 <= 終了です: start={start} end={end}"
            )
        # ``start <= end`` なので終了だけを見れば両端が上界の内側に入る。
        if end > MAX_POSITION:
            raise ValueError(
                f"足の区間の位置は {MAX_POSITION} 以下です: start={start} end={end}"
            )
        # 上限判定は読みの**前**に置く。
        rows = self._source.count(job_id, start=start, end=end)
        if rows > MAX_RETURNED_BARS:
            raise ChartBarsRangeTooWideError(
                f"区間に入る足が {rows} 本あり上限 {MAX_RETURNED_BARS} 本を超えます。"
                "区間を狭めてください（間引きは行いません）"
            )
        declared = self._source.declaration(job_id)
        columns = self._source.read(
            job_id, columns=declared.columns, start=start, end=end
        )
        return ChartBarsRows(
            start=start,
            end=end,
            rows=len(columns[declared.index_column]),
            columns=columns,
        )
