"""期末清算（end_of_test）後の口座が足ごとの口座記録の最終行に載ること。

欠陥（2026-09-26 実測・ジョブ 91291405477e41549c818dcc4176ef2d）:
    観測（`RunTracePort.observe`）は評価点ループの中だけで呼ばれ、期末清算はループの後で
    行われる。そのため足ごとの口座の最終行は**清算前**の口座で、上のチャートの残高は 8775・
    レポート（balance_curve）は 8765 と食い違った（清算 trade 168 の −10 が欠落）。

観測の境界:
    エンジンの観測口（`run_tracer`）へ記録器を注入する（宣言された境界）。内部名は差し替えない。

計算量（回数をリテラルで焼き込まない）:
    清算の通知は「清算が起きたか」だけで決まり、足の数を増やしても増えないこと・
    足ごとの記録の行数が足の数から増えないこと（作って捨てる行が無い）を 2 点以上で表明する。
"""
from __future__ import annotations

import pytest

from simulator.adapter.trace.account_curve import AccountCurveRecorder
from simulator.usecase.run_trace_ports import FanOutRunTrace, RunTracePort
from simulator.tests.unit.test_run_trace_observation import _orders_strategy, _run


class _SettlementSpy(RunTracePort):
    """清算の通知を数える Spy（評価点の観測は数えるだけ）。"""

    def __init__(self) -> None:
        self.settlements: "list[int]" = []
        self.bar_indices: "set[int]" = set()

    def observe(self, point, account, open_trades, halted):
        self.bar_indices.add(point.bar_index)

    def observe_final_settlement(self, bar_index, bar, account):
        self.settlements.append(bar_index)


def _run_with(bar_count, *tracers, strategy=None):
    tracer = tracers[0] if len(tracers) == 1 else FanOutRunTrace(*tracers)
    result, _tracer, _schedule = _run(
        bar_count=bar_count,
        strategy=strategy if strategy is not None else _orders_strategy(),
        tracer=tracer,
    )
    return result


class TestTheLastRowIsTheSettledAccount:
    def test_the_last_balance_matches_the_balance_curve(self) -> None:
        # Arrange: バー 4 で買った玉が期末まで残る＝期末清算が起きる run。
        recorder = AccountCurveRecorder()

        # Act
        result = _run_with(8, recorder)

        # Assert
        assert result.trades[-1].exit_reason == "end_of_test"
        assert recorder.balance[-1] == result.balance_curve[-1]

    def test_the_last_row_holds_no_floating_pnl_and_no_margin(self) -> None:
        recorder = AccountCurveRecorder()

        _run_with(8, recorder)

        assert recorder.equity[-1] == recorder.balance[-1]
        assert recorder.margin[-1] == 0.0
        assert recorder.margin_level[-1] is None


class TestComplexity:
    @pytest.mark.parametrize("bar_count", [8, 40])
    def test_one_settlement_notice_per_settlement_regardless_of_bars(self, bar_count) -> None:
        # Arrange
        spy = _SettlementSpy()

        # Act
        result = _run_with(bar_count, spy)

        # Assert: 通知の数＝清算が起きた回数（run につき高々 1）。足の数に比例しない。
        settled = 1 if result.trades and result.trades[-1].exit_reason == "end_of_test" else 0
        assert len(spy.settlements) == settled == 1
        assert spy.settlements == [max(spy.bar_indices)]

    def test_no_notice_without_a_position_left(self) -> None:
        from simulator.tests.unit.test_run_backtest_single_engine import _NullStrategy

        spy = _SettlementSpy()

        result = _run_with(8, spy, strategy=_NullStrategy())

        assert not result.trades
        assert spy.settlements == []

    @pytest.mark.parametrize("bar_count", [8, 40])
    def test_rows_stay_one_per_observed_bar(self, bar_count) -> None:
        # Arrange: 行数の期待値は同じ run の観測から導く（リテラルで焼き込まない）。
        recorder, spy = AccountCurveRecorder(), _SettlementSpy()

        # Act
        _run_with(bar_count, recorder, spy)

        # Assert: 清算の通知は既存の最終行を書き換えるだけで、行を足さない。
        assert recorder.rows == len(spy.bar_indices)
