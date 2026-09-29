"""AccountCurveRecorder — 足ごとの残高・有効証拠金を記録する観測器（RunTracePort 実装）。

複数の観測器を同時に使うときの合成は Port と同じ層の「`FanOutRunTrace`」（`simulator/usecase/run_trace_ports.py`）。

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

from simulator.usecase.bar_times import epoch_seconds_of
from simulator.usecase.run_trace_ports import RunTracePort


class AccountCurveRecorder(RunTracePort):
    """評価点の観測を足ごとの (時刻, 残高, 有効証拠金, 必要証拠金, 証拠金維持率) へ畳む。

    証拠金維持率は ``Account.margin_level()``（有効証拠金 ÷ 必要証拠金 × 100）をそのまま写す。
    保有が無い足は必要証拠金 0 で維持率が定義されない（口座は ∞ を返す）ので ``None`` にする
    （∞ を数値として描くと軸が壊れ、0 に置き換えると「維持率 0%」という偽の値になる）。
    """

    #: 記録の単位（「`OBSERVATION_UNITS`」 の語彙）。本実装は足 1 本につき 1 行。
    OBSERVATION_UNIT = "bar"

    def __init__(self) -> None:
        self._bar_index: "int | None" = None
        # 足の時刻は受け取った表現のまま溜め、読むときに列ごと一括で epoch 秒へ変換する
        #   （ISSUE-553 項目 3: 足ごとに変換すると 215 万本で 5.4 秒）。
        self._bar_times: "list[Any]" = []
        self._times: "list[int] | None" = None
        self.balance: "list[float]" = []
        self.equity: "list[float]" = []
        self.margin: "list[float]" = []
        self.margin_level: "list[float | None]" = []

    def observe(self, point: Any, account: Any, open_trades: Any, halted: bool) -> None:
        self._record(point.bar_index, point.bar, account)

    def observe_final_settlement(self, bar_index: int, bar: Any, account: Any) -> None:
        """期末清算後の口座で最終足の行を上書きする（清算は最終足の中で起きる＝足の終わりの口座）。"""
        self._record(bar_index, bar, account)

    def _record(self, bar_index: int, bar: Any, account: Any) -> None:
        row = (
            float(account.balance),
            float(account.equity),
            float(account.margin),
            _finite_or_none(account.margin_level()),
        )
        if bar_index != self._bar_index:
            self._bar_index = bar_index
            self._bar_times.append(bar.time)
            self._times = None
            for column in self._columns():
                column.append(None)
        for column, value in zip(self._columns(), row):
            column[-1] = value

    @property
    def times(self) -> "list[int]":
        """各行の足の時刻（epoch 秒）。変換は列ごとに 1 回（記録が増えたら次に読むとき変換し直す）。"""
        if self._times is None:
            self._times = epoch_seconds_of(self._bar_times)
        return self._times

    @property
    def rows(self) -> int:
        """記録行数（＝観測した足の数。列はすべて同じ長さである）。"""
        return len(self._bar_times)

    def _columns(self) -> "tuple[list, ...]":
        return (self.balance, self.equity, self.margin, self.margin_level)


def _finite_or_none(value: float) -> "float | None":
    return float(value) if math.isfinite(value) else None
