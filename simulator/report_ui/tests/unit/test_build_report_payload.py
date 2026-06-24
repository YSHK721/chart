"""BuildReportPayload UC 単体テスト（詳細設計 §4.8・§5.3・§8.1）。

BacktestResult(モック)→payload。summary 各式・degradation・verdict 4分岐＋reason 2条件、
および致命3の回帰固定（len(trades)==len(balance_curve) / balance_curve[i].time==trades[i].exit_time）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from simulator.report_ui.usecase.build_report_payload import BuildReportPayload
from simulator.report_ui.usecase.report_models import ReportPayloadModel


# --- テストダブル -----------------------------------------------------------

@dataclass
class _FakeTrade:
    side: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    volume: float
    exit_reason: str
    _pnl: float

    def pnl(self):
        return self._pnl


@dataclass
class _FakeStats:
    profit: float
    trades: int
    initial_deposit: float = 10000.0


@dataclass
class _FakeResult:
    trades: Any
    balance_curve: Any
    stats: Any
    deals: Any = None
    equity_curve: Any = None


@dataclass
class _FakeBar:
    time: int
    high: float
    low: float
    open: float = 0.0
    close: float = 0.0


def _spec():
    @dataclass
    class S:
        point_size: float = 0.1
        digits: int = 1
        stops_level: int = 0
    return S()


def _ea_params():
    return {"sl_points": 200, "tp_points": 500}


def _meta(seg):
    return {
        "symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
        "period": "2026.04.01-04.14" if seg == "is" else "2026.04.15-04.23",
        "label": "IS" if seg == "is" else "OOS",
    }


def _make_result(profits, exit_times, balances, side="buy"):
    """profits/exit_times/balances から FakeResult を組む（1:1）。"""
    trades = []
    et0 = 1000
    for i, (p, xt) in enumerate(zip(profits, exit_times)):
        trades.append(_FakeTrade(
            side=side, entry_time=et0 + i, exit_time=xt,
            entry_price=39402.0, exit_price=39402.0 + p, volume=0.1,
            exit_reason="tp" if p > 0 else "sl", _pnl=p,
        ))
    stats = _FakeStats(profit=sum(profits), trades=len(trades))
    return _FakeResult(trades=trades, balance_curve=list(balances), stats=stats)


def _run(result_is, result_oos, bars_is=None, bars_oos=None):
    bars_is = bars_is if bars_is is not None else []
    bars_oos = bars_oos if bars_oos is not None else []
    return BuildReportPayload().execute(
        result_is=result_is, result_oos=result_oos,
        bars_is=bars_is, bars_oos=bars_oos,
        spec=_spec(), ea_params=_ea_params(),
        meta_is=_meta("is"), meta_oos=_meta("oos"),
    )


# --- 戻り型・形状 -----------------------------------------------------------

class TestPayloadShape:
    def test_returns_payload_model_with_both_segments(self):
        r = _make_result([100.0], [2000], [10100.0])
        payload = _run(r, r)
        assert isinstance(payload, ReportPayloadModel)
        assert set(payload.segments.keys()) == {"is", "oos"}
        assert set(payload.summary.keys()) == {"is", "oos"}


# --- 致命3 回帰固定（最優先） ------------------------------------------------

class TestCritical3Regression:
    def test_trades_len_equals_balance_curve_len(self):
        r = _make_result([10.0, -5.0, 20.0], [2000, 3000, 4000],
                         [10010.0, 10005.0, 10025.0])
        payload = _run(r, r)
        seg = payload.segments["is"]
        assert len(seg.trades) == len(seg.agg["balance_curve"])

    def test_balance_curve_time_equals_trade_exit_time(self):
        r = _make_result([10.0, -5.0, 20.0], [2000, 3000, 4000],
                         [10010.0, 10005.0, 10025.0])
        payload = _run(r, r)
        seg = payload.segments["is"]
        for i, tr in enumerate(seg.trades):
            assert seg.agg["balance_curve"][i]["time"] == tr.exit_time

    def test_balance_curve_value_equals_running_balance(self):
        r = _make_result([10.0, -5.0], [2000, 3000], [10010.0, 10005.0])
        payload = _run(r, r)
        seg = payload.segments["is"]
        assert [p["value"] for p in seg.agg["balance_curve"]] == [10010.0, 10005.0]

    def test_mismatched_balance_len_raises(self):
        # trades 3 件・balance 2 件 → 致命3 1:1 違反で ValueError
        bad = _make_result([1.0, 2.0, 3.0], [10, 20, 30], [10001.0, 10002.0])
        with pytest.raises(ValueError):
            _run(bad, bad)


# --- summary 算出式（§4.8） --------------------------------------------------

class TestSummary:
    def test_net_and_final_balance(self):
        r = _make_result([100.0, -40.0], [2000, 3000], [10100.0, 10060.0])
        s = _run(r, r).summary["is"]
        assert s.trades == 2
        assert s.net == 60.0
        assert s.final_balance == 10060.0

    def test_win_rate_profit_gt_zero(self):
        # 3 勝(>0) 1 負 → 75.0
        r = _make_result([10.0, 20.0, 30.0, -5.0], [1, 2, 3, 4],
                         [10010.0, 10030.0, 10060.0, 10055.0])
        s = _run(r, r).summary["is"]
        assert s.win_rate == 75.0

    def test_profit_factor(self):
        # gp=30, gl=-10 → 3.0
        r = _make_result([10.0, 20.0, -10.0], [1, 2, 3],
                         [10010.0, 10030.0, 10020.0])
        s = _run(r, r).summary["is"]
        assert s.profit_factor == 3.0

    def test_profit_factor_inf_when_no_loss(self):
        # gl=0 → inf
        import math
        r = _make_result([10.0, 20.0], [1, 2], [10010.0, 10030.0])
        s = _run(r, r).summary["is"]
        assert math.isinf(s.profit_factor)

    def test_expectancy(self):
        r = _make_result([10.0, -4.0], [1, 2], [10010.0, 10006.0])
        s = _run(r, r).summary["is"]
        assert s.expectancy == 3.0  # net=6 / n=2

    def test_return_pct(self):
        r = _make_result([100.0], [1], [10100.0])
        s = _run(r, r).summary["is"]
        assert s.return_pct == 1.0  # (10100-10000)/10000*100


# --- degradation（§5.3） ----------------------------------------------------

class TestDegradation:
    def test_ratio_and_delta(self):
        is_r = _make_result([100.0], [1], [10100.0])
        oos_r = _make_result([50.0], [1], [10050.0])
        deg = _run(is_r, oos_r).degradation
        assert deg["net"]["is"] == 100.0
        assert deg["net"]["oos"] == 50.0
        assert deg["net"]["ratio"] == 0.5
        assert deg["net"]["delta"] == -50.0

    def test_ratio_null_when_is_zero(self):
        # IS net=0（勝ち負け相殺）→ ratio None
        is_r = _make_result([10.0, -10.0], [1, 2], [10010.0, 10000.0])
        oos_r = _make_result([5.0, -10.0], [1, 2], [10005.0, 9995.0])
        deg = _run(is_r, oos_r).degradation
        assert deg["net"]["ratio"] is None


# --- verdict 判定木（§5.3・順序厳守） ----------------------------------------

class TestVerdict:
    def test_fail_is_profit_oos_loss(self):
        # IS net>0, OOS net<=0 → fail（最優先分岐）
        is_r = _make_result([100.0], [1], [10100.0])
        oos_r = _make_result([-50.0], [1], [9950.0])
        v = _run(is_r, oos_r).verdict
        assert v.result == "fail"
        assert any("優位性消失" in r for r in v.reasons)

    def test_fail_oos_pf_below_one(self):
        # IS net<=0 だが OOS PF<1.0 → fail（2番目分岐）
        is_r = _make_result([-10.0], [1], [9990.0])
        oos_r = _make_result([10.0, -30.0], [1, 2], [10010.0, 9980.0])
        v = _run(is_r, oos_r).verdict
        assert v.result == "fail"

    def test_warn_pf_ratio_below_07(self):
        # IS PF 高 / OOS PF 1.0以上だが IS比<0.7 → warn
        is_r = _make_result([100.0, -10.0], [1, 2], [10100.0, 10090.0])  # PF=10
        oos_r = _make_result([12.0, -10.0], [1, 2], [10012.0, 10002.0])  # PF=1.2, ratio 0.12
        v = _run(is_r, oos_r).verdict
        assert v.result == "warn"

    def test_pass_when_robust(self):
        # IS=OOS で劣化なし → pass
        r = _make_result([100.0, -10.0], [1, 2], [10100.0, 10090.0])
        v = _run(r, r).verdict
        assert v.result == "pass"
        assert any("優位性を維持" in x for x in v.reasons)

    def test_reason_winrate_delta_below_minus5(self):
        # win_rate が 5pt 超悪化 → 追加 reason
        is_r = _make_result([10.0, 20.0, 30.0, 40.0], [1, 2, 3, 4],
                            [10010.0, 10030.0, 10060.0, 10100.0])  # win 100%
        oos_r = _make_result([10.0, -5.0], [1, 2], [10010.0, 10005.0])  # win 50%
        v = _run(is_r, oos_r).verdict
        assert any("勝率差" in r for r in v.reasons)


# --- trades 派生（sl/tp/id/hold/comment） ------------------------------------

class TestTradeRows:
    def test_id_is_one_based_index(self):
        r = _make_result([10.0, 20.0], [2000, 3000], [10010.0, 10030.0])
        seg = _run(r, r).segments["is"]
        assert [t.id for t in seg.trades] == [1, 2]
        assert [t.order for t in seg.trades] == [1, 2]

    def test_hold_sec_is_exit_minus_entry(self):
        r = _make_result([10.0], [2000], [10010.0])
        seg = _run(r, r).segments["is"]
        # entry_time=1000, exit_time=2000 → 1000
        assert seg.trades[0].hold_sec == 1000

    def test_comment_maps_exit_reason(self):
        r = _make_result([10.0, -5.0], [2000, 3000], [10010.0, 10005.0])
        seg = _run(r, r).segments["is"]
        # profit>0 → exit_reason tp → comment "tp" ; profit<0 → sl
        assert seg.trades[0].comment == "tp"
        assert seg.trades[1].comment == "sl"

    def test_sl_tp_derived_for_buy(self):
        r = _make_result([10.0], [2000], [10010.0], side="buy")
        seg = _run(r, r).segments["is"]
        # entry_price 39402.0, SL200/TP500/point0.1 → sl=39382.0, tp=39452.0
        assert seg.trades[0].sl == "39382.0"
        assert seg.trades[0].tp == "39452.0"
