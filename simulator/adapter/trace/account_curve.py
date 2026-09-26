"""AccountCurveRecorder — 足ごとの残高・有効証拠金を記録する観測器（RunTracePort 実装）。

何を解くか:
    run の結果が持つ残高の推移（「`balance_curve`」）は**決済のたびに 1 点**であり、保有中の
    推移（含み損益を含む有効証拠金）は時刻つきではどこにも残っていない。チャートの資産パネルを
    「保有中も更新」するには、足ごとの値が要る（依頼者指示 2026-09-26）。

記録の単位:
    評価点ではなく**足**である。1 足に評価点が複数ある（every_tick 等）ときは、その足の
    **最後の評価点**の値で上書きする（＝足の終わりの口座）。評価点ごとに行を作ると、
    JP225 1 か月で 95 万行（実測 952,832 回の観測）を作って足の数へ畳み直すことになる。

契約（`RunTracePort`）: 口座と保有列は読むだけで、値を写して持つ。戻り値は持たない。
"""
from __future__ import annotations

import math
from typing import Any

from simulator.domain.bar_time import epoch_seconds
from simulator.usecase.run_trace_ports import RunTracePort


class AccountCurveRecorder(RunTracePort):
    """評価点の観測を足ごとの (時刻, 残高, 有効証拠金, 必要証拠金, 証拠金維持率) へ畳む。

    証拠金維持率は ``Account.margin_level()``（有効証拠金 ÷ 必要証拠金 × 100）をそのまま写す。
    保有が無い足は必要証拠金 0 で維持率が定義されない（口座は ∞ を返す）ので ``None`` にする
    （∞ を数値として描くと軸が壊れ、0 に置き換えると「維持率 0%」という偽の値になる）。
    """

    def __init__(self) -> None:
        self._bar_index: "int | None" = None
        self.times: "list[int]" = []
        self.balance: "list[float]" = []
        self.equity: "list[float]" = []
        self.margin: "list[float]" = []
        self.margin_level: "list[float | None]" = []

    def observe(self, point: Any, account: Any, open_trades: Any, halted: bool) -> None:
        row = (
            float(account.balance),
            float(account.equity),
            float(account.margin),
            _finite_or_none(account.margin_level()),
        )
        if point.bar_index != self._bar_index:
            self._bar_index = point.bar_index
            self.times.append(epoch_seconds(point.bar.time))
            for column in self._columns():
                column.append(None)
        for column, value in zip(self._columns(), row):
            column[-1] = value

    def _columns(self) -> "tuple[list, ...]":
        return (self.balance, self.equity, self.margin, self.margin_level)


def _finite_or_none(value: float) -> "float | None":
    return float(value) if math.isfinite(value) else None


class FanOutRunTrace(RunTracePort):
    """1 つの観測を複数の観測器へ配る（エンジンの観測口は 1 つなので合成で束ねる）。"""

    def __init__(self, *tracers: RunTracePort) -> None:
        self._tracers = tracers

    def observe(self, point: Any, account: Any, open_trades: Any, halted: bool) -> None:
        for tracer in self._tracers:
            tracer.observe(point, account, open_trades, halted)
