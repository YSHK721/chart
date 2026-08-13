"""Phase 7 FR-08: 部分決済 exit が report.json の trades へ反映されることの回帰テスト。

部分決済は独立した TradeRecord（volume=決済分・残玉は別トレード）として run_backtest が
trades/balance_curve をペアで積む。build_report_payload は trades を 1:1 で写すため、
部分 exit と残玉決済がそれぞれ独立行として現れ、各行の volume が決済分を反映する。
"""
from __future__ import annotations

from simulator.report_ui.tests.unit.test_build_report_payload import (
    _FakeResult,
    _FakeStats,
    _FakeTrade,
    _ea_params,
    _meta,
    _spec,
)
from simulator.report_ui.usecase.build_report_payload import BuildReportPayload


def _partial_result():
    # 同一建玉（entry_time=1000）を 0.05 部分決済（@+80）→ 残玉 0.05 を SL 決済（@-30）。
    trades = [
        _FakeTrade(side="buy", entry_time=1000, exit_time=2000, entry_price=39402.0,
                   exit_price=39482.0, volume=0.05, exit_reason="partial", _pnl=4.0),
        _FakeTrade(side="buy", entry_time=1000, exit_time=3000, entry_price=39402.0,
                   exit_price=39372.0, volume=0.05, exit_reason="sl", _pnl=-1.5),
    ]
    stats = _FakeStats(profit=2.5, trades=2)
    return _FakeResult(trades=trades, balance_curve=[10004.0, 10002.5], stats=stats)


def test_partial_exit_and_residual_are_separate_rows():
    payload = BuildReportPayload().execute_single(
        result=_partial_result(), bars=[], spec=_spec(),
        ea_params=_ea_params(), meta=_meta("is"),
    )
    rows = payload.segments["single"].trades
    assert len(rows) == 2
    # 各行の volume が決済分（0.05）を反映する。
    assert [str(r.volume) for r in rows] == ["0.05", "0.05"]
    # 部分 exit（tp）と残玉決済（sl）が独立行として現れる。
    reasons = {r.comment for r in rows}
    assert len(reasons) == 2
