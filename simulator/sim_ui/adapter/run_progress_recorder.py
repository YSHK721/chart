"""RunProgressRecorder — 実行中の進み具合（％）を知らせる観測器（RunTracePort 実装）。

なぜ在るか（2026-09-27 依頼者指示）: 「シミュレーションの結果待ちのとき終わりが分からない」。
    進み具合＝処理を終えた足の本数 ÷ run の足の総数（`observe_start` が渡す）。

知らせるのは**整数の％が変わったときだけ**である。評価点は every_tick で 1 か月 95 万回
    （実測・account_curve.py）あり、点ごとに知らせると同じ値を 95 万回書いて捨てる。
    ％は 0〜100 の 101 通りなので、知らせる回数は足の本数・評価点の数に依らず 101 回以下。

契約（`RunTracePort`）: 読むのは `point.bar_index` だけ。口座・保有列には触れない。
知らせ先は注入する（ファイルへ書くのは job-dir を知る側・simulator/sim_ui/adapter/run_progress_file.py）。
"""
from __future__ import annotations

from typing import Any, Callable

from simulator.usecase.run_trace_ports import RunTracePort


class RunProgressRecorder(RunTracePort):
    """足の処理割合を整数の％で `publish` へ渡す（値が変わったときだけ）。"""

    def __init__(self, publish: "Callable[[int], None]") -> None:
        self._publish = publish
        self._bar_count = 0
        self._last: "int | None" = None

    def observe_start(self, bar_count: int) -> None:
        self._bar_count = int(bar_count)
        self._emit(0)

    def observe(self, point: Any, account: Any, open_trades: Any, halted: bool) -> None:
        if self._bar_count <= 0:
            return
        # 足の最後の評価点を処理し終えた時点で、その足までが済んだ（bar_index + 1 本）。
        #   足の途中の点でも同じ値になるので、同じ％は下の比較で捨てる。
        self._emit((point.bar_index + 1) * 100 // self._bar_count)

    def _emit(self, percent: int) -> None:
        if percent == self._last:
            return
        self._last = percent
        self._publish(percent)
