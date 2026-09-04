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
    # report 写像（§4.5）が stats を read-only 直引きするため、実 BacktestStats と同名の
    # 保持フィールドを default 付きで全て備える（既存テストは profit/trades のみ指定すれば足りる）。
    profit: float
    trades: int
    initial_deposit: float = 10000.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    recovery_factor: float = 0.0
    sharpe_ratio: float = 0.0
    expected_payoff: float = 0.0
    ahpr: float = 0.0
    profit_trades: int = 0
    loss_trades: int = 0
    long_trades: int = 0
    short_trades: int = 0
    profit_long_trades: int = 0
    profit_short_trades: int = 0
    max_profit_trade: float = 0.0
    average_profit_trade: float = 0.0
    max_loss_trade: float = 0.0
    average_loss_trade: float = 0.0
    max_con_wins: int = 0
    max_con_profit_trades: float = 0.0
    max_con_losses: int = 0
    max_con_loss_trades: float = 0.0
    con_profit_max: float = 0.0
    con_profit_max_trades: int = 0
    con_loss_max: float = 0.0
    con_loss_max_trades: int = 0
    profit_trades_avg_con: float = 0.0
    loss_trades_avg_con: float = 0.0
    balance_dd_abs: float = 0.0
    balance_dd: float = 0.0
    balance_dd_percent: float = 0.0
    balance_dd_relative: float = 0.0
    balance_ddrel_percent: float = 0.0
    equity_dd_abs: float = 0.0
    equity_dd_max: float = 0.0
    equity_dd_max_percent: float = 0.0
    z_score: float = 0.0


@dataclass
class _FakeResult:
    trades: Any
    balance_curve: Any
    stats: Any
    deals: Any = None
    equity_curve: Any = None


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


# --- agg.heat 結合（§4.7・F-3 / アーキ指針 §1・§6 OCP） -----------------------

# 既知 UTC entry_time（R-2 検証用。test_derive と同規約）。
_TS_MON_H0 = 1776643200   # 2026-04-20 00:00:00 UTC（weekday()=0→"Mon"）
_TS_MON_H23 = 1776726000  # 2026-04-20 23:00:00 UTC


def _make_result_with_entry(profits, entry_times, exit_times, balances, side="buy"):
    """entry_time を明示指定して FakeResult を組む（heat の wday|hour 分類検証用）。"""
    trades = []
    for p, et, xt in zip(profits, entry_times, exit_times):
        trades.append(_FakeTrade(
            side=side, entry_time=et, exit_time=xt,
            entry_price=39402.0, exit_price=39402.0 + p, volume=0.1,
            exit_reason="tp" if p > 0 else "sl", _pnl=p,
        ))
    stats = _FakeStats(profit=sum(profits), trades=len(trades))
    return _FakeResult(trades=trades, balance_curve=list(balances), stats=stats)


class TestAggHeat:
    def test_is_and_oos_heat_both_nonempty(self):
        # OCP 対称: IS/OOS 両区間で heat が同一経路で実体化（両非空）
        r = _make_result_with_entry(
            [10.0, -5.0], [_TS_MON_H0, _TS_MON_H23], [2000, 3000],
            [10010.0, 10005.0])
        payload = _run(r, r)
        assert len(payload.segments["is"].agg["heat"]) > 0
        assert len(payload.segments["oos"].agg["heat"]) > 0

    def test_heat_cell_count_sum_equals_trades(self):
        # 件数整合: Σ cell.count == len(trades)
        r = _make_result_with_entry(
            [10.0, -5.0, 20.0], [_TS_MON_H0, _TS_MON_H0, _TS_MON_H23],
            [2000, 3000, 4000], [10010.0, 10005.0, 10025.0])
        seg = _run(r, r).segments["is"]
        total = sum(c["count"] for c in seg.agg["heat"])
        assert total == len(seg.trades)

    def test_heat_representative_cell_matches_manual(self):
        # 代表セル一致: Mon hour0 に 2 取引(profit 30,-10; 1勝) → profit=20.0,count=2,wins=1
        r = _make_result_with_entry(
            [30.0, -10.0], [_TS_MON_H0, _TS_MON_H0], [2000, 3000],
            [10030.0, 10020.0])
        seg = _run(r, r).segments["is"]
        by = {(c["wday"], c["hour"]): c for c in seg.agg["heat"]}
        cell = by[("Mon", 0)]
        assert cell["profit"] == 20.0
        assert cell["count"] == 2
        assert cell["wins"] == 1

    def test_agg_keys_shape_unchanged(self):
        # 形状不変（OCP）: agg のキー集合は②④を通じ不変（④で entries/pl/scatter/hold を実体化）
        r = _make_result_with_entry([10.0], [_TS_MON_H0], [2000], [10010.0])
        agg = _run(r, r).segments["is"].agg
        assert set(agg.keys()) == {
            "entries_hour", "entries_session", "entries_wday", "entries_month",
            "pl_hour", "pl_wday", "pl_month", "balance_curve",
            "scatter_mfe", "scatter_mae", "hold_pl", "hold_cnt",
            "weekorder", "heat",
        }
        # ④で実体化: scatter は trade ごとの点列・hold は 7 区分の損益 dict（空 assertion を差替）
        assert agg["scatter_mfe"] == [{"x": agg["scatter_mfe"][0]["x"], "y": 10.0, "id": 1}]
        assert set(agg["hold_pl"].keys()) == {
            "<1m", "1-2m", "2-5m", "5-10m", "10-30m", "30-60m", ">1h"}

    def test_heat_built_via_derive_not_inline(self):
        # 指針1: UC は derive.heat_cells を呼ぶ組立のみ（loop 内で時刻分解を直書きしない）。
        # 同一 (entry_time, profit) を derive.heat_cells へ直接渡した結果とセル集合が一致する。
        from simulator.report_ui.usecase import derive
        entry_times = [_TS_MON_H0, _TS_MON_H23, _TS_MON_H0]
        profits = [30.0, -10.0, 5.0]
        r = _make_result_with_entry(profits, entry_times, [2000, 3000, 4000],
                                    [10030.0, 10020.0, 10025.0])
        uc_cells = _run(r, r).segments["is"].agg["heat"]
        direct = derive.heat_cells(zip(entry_times, profits))
        norm = lambda cs: sorted(
            (c["wday"], c["hour"], c["profit"], c["count"], c["wins"]) for c in cs)
        assert norm(uc_cells) == norm(direct)


class TestAggFull:
    """④ entries/pl/scatter/hold の実体化（空プレースホルダ→derive 組立）。"""

    def test_all_agg_entities_nonempty_both_segments(self):
        # IS/OOS 両区間で entries/pl/scatter/hold が実体化（OCP 対称）
        r = _make_result_with_entry(
            [10.0, -5.0], [_TS_MON_H0, _TS_MON_H23], [2000, 3000],
            [10010.0, 10005.0])
        for seg in ("is", "oos"):
            agg = _run(r, r).segments[seg].agg
            assert agg["entries_hour"][0] == 1
            assert agg["entries_session"]["Asia"] == 1
            assert len(agg["scatter_mfe"]) == 2
            assert len(agg["scatter_mae"]) == 2
            assert sum(agg["hold_cnt"].values()) == 2

    def test_entries_use_entry_time_pl_use_exit_time(self):
        # 基準差固定（最重要・アーキ指針 §1）: 同一 trade で entry hour と exit hour が異なる。
        # entry_time=Mon hour0 / exit_time=Mon hour23 → entries は hour0、pl は hour23 に分かれる。
        r = _make_result_with_entry(
            [10.0], [_TS_MON_H0], [_TS_MON_H23], [10010.0])
        agg = _run(r, r).segments["is"].agg
        # entries 系は entry_time(hour0) に積まれる
        assert agg["entries_hour"][0] == 1
        assert agg["entries_hour"][23] == 0
        # pl 系は exit_time(hour23) に積まれる（同一 trade でもバケットが分かれる）
        assert agg["pl_hour"][23] == 10.0
        assert agg["pl_hour"][0] == 0.0

    def test_entries_count_integrity_equals_trades(self):
        # 件数整合: Σ entries_hour == len(trades)
        r = _make_result_with_entry(
            [10.0, -5.0, 20.0], [_TS_MON_H0, _TS_MON_H0, _TS_MON_H23],
            [2000, 3000, 4000], [10010.0, 10005.0, 10025.0])
        seg = _run(r, r).segments["is"]
        assert sum(seg.agg["entries_hour"].values()) == len(seg.trades)

    def test_scatter_ids_match_trade_ids(self):
        # scatter の id は trade id（1始点 index）に一致
        r = _make_result_with_entry(
            [10.0, -5.0], [_TS_MON_H0, _TS_MON_H23], [2000, 3000],
            [10010.0, 10005.0])
        seg = _run(r, r).segments["is"]
        scat_ids = sorted(p["id"] for p in seg.agg["scatter_mfe"])
        assert scat_ids == sorted(t.id for t in seg.trades)

    def test_agg_built_via_derive_not_inline(self):
        # 指針1: UC は derive 純関数を呼ぶ組立のみ（loop 直書きしない）。
        # 同一入力を derive へ直接渡した結果と UC の entries/pl が一致する。
        from simulator.report_ui.usecase import derive
        entry_times = [_TS_MON_H0, _TS_MON_H23]
        exit_times = [_TS_MON_H23, _TS_MON_H0]
        profits = [10.0, -5.0]
        r = _make_result_with_entry(profits, entry_times, exit_times,
                                    [10010.0, 10005.0])
        agg = _run(r, r).segments["is"].agg
        assert agg["entries_hour"] == derive.entries_buckets(entry_times)["hour"]
        assert agg["pl_hour"] == derive.pl_buckets(zip(exit_times, profits))["hour"]

    def test_empty_trades_keep_empty_aggs(self):
        # 空 trades: scatter は空列・hold/entries は 0 埋め維持（致命-3 1:1 を満たす空 result）
        r = _make_result([], [], [])
        agg = _run(r, r).segments["is"].agg
        assert agg["scatter_mfe"] == []
        assert sum(agg["entries_hour"].values()) == 0
        assert sum(agg["hold_cnt"].values()) == 0


# --- segments.{is,oos}.report 実体化（⑤ R-1・§4.5 BacktestStats→report 写像） -----

@dataclass
class _FullStats:
    """§4.5 写像検証用に BacktestStats の保持フィールドを網羅した stats ダブル。

    report 写像は stats を read-only 直引きするため、実 BacktestStats と同名の属性を
    持つダブルで十分（domain 非依存・写像の組立ロジックのみを検証する）。
    """
    initial_deposit: float = 10000.0
    profit: float = 11370.0
    gross_profit: float = 84600.0
    gross_loss: float = -73230.0
    profit_factor: float = 1.155
    recovery_factor: float = 3.21
    sharpe_ratio: float = 0.42
    expected_payoff: float = 2.18
    ahpr: float = 1.0002
    trades: int = 5224
    profit_trades: int = 2950
    loss_trades: int = 2274
    long_trades: int = 2600
    short_trades: int = 2624
    profit_long_trades: int = 1480
    profit_short_trades: int = 1470
    max_profit_trade: float = 50.0
    average_profit_trade: float = 28.68
    max_loss_trade: float = -20.0
    average_loss_trade: float = -32.2
    max_con_wins: int = 12
    max_con_profit_trades: float = 360.0
    max_con_losses: int = 9
    max_con_loss_trades: float = -180.0
    con_profit_max: float = 420.0
    con_profit_max_trades: int = 11
    con_loss_max: float = -240.0
    con_loss_max_trades: int = 8
    profit_trades_avg_con: float = 3.0
    loss_trades_avg_con: float = 2.0
    balance_dd_abs: float = 0.0
    balance_dd: float = 2400.0
    balance_dd_percent: float = 10.5
    balance_dd_relative: float = 2400.0
    balance_ddrel_percent: float = 10.5
    equity_dd_abs: float = 0.0
    equity_dd_max: float = 2600.0
    equity_dd_max_percent: float = 11.2
    z_score: float = -1.34


def _result_with_stats(stats, profits=(10.0, -5.0), exit_times=(2000, 3000),
                       balances=(10010.0, 10005.0)):
    """report 写像検証用に任意 stats を載せた FakeResult を組む（trades は最小・1:1）。"""
    r = _make_result(list(profits), list(exit_times), list(balances))
    return _FakeResult(trades=r.trades, balance_curve=r.balance_curve, stats=stats)


def _run_report(stats_is, stats_oos=None):
    stats_oos = stats_oos if stats_oos is not None else stats_is
    return _run(_result_with_stats(stats_is), _result_with_stats(stats_oos))


class TestSegmentReport:
    """§4.5 BacktestStats→report ラベル dict 写像（保持指標 set・欠落 skip・文字列整形）。"""

    def test_report_is_nonempty_for_both_segments(self):
        # R-1: IS/OOS 両 report が実体化（空でない）。
        p = _run_report(_FullStats())
        assert len(p.segments["is"].report) > 0
        assert len(p.segments["oos"].report) > 0

    def test_total_net_profit_formatted_as_integer_string(self):
        # §4.5: Total Net Profit = f"{stats.profit:.0f}"（文字列）。
        seg = _run_report(_FullStats(profit=11370.0)).segments["is"]
        assert seg.report["Total Net Profit"] == "11370"

    def test_profit_factor_two_decimals(self):
        # §4.5: Profit Factor = f"{v:.2f}"。
        seg = _run_report(_FullStats(profit_factor=1.155)).segments["is"]
        assert seg.report["Profit Factor"] == "1.16"

    def test_sharpe_and_recovery_and_payoff_two_decimals(self):
        seg = _run_report(_FullStats(
            sharpe_ratio=0.42, recovery_factor=3.21, expected_payoff=2.18)).segments["is"]
        assert seg.report["Sharpe Ratio"] == "0.42"
        assert seg.report["Recovery Factor"] == "3.21"
        assert seg.report["Expected Payoff"] == "2.18"

    def test_ahpr_four_decimals(self):
        # §4.5: AHPR = f"{v:.4f}"。
        seg = _run_report(_FullStats(ahpr=1.0002)).segments["is"]
        assert seg.report["AHPR"] == "1.0002"

    def test_total_trades_is_count(self):
        seg = _run_report(_FullStats(trades=5224)).segments["is"]
        assert seg.report["Total Trades"] == "5224"

    def test_profit_trades_percent_and_count(self):
        # §4.5: Profit Trades (% of total) = f"{pct:.2f}% ({n})"・pct=profit_trades/trades*100。
        seg = _run_report(_FullStats(trades=5224, profit_trades=2950)).segments["is"]
        # 2950/5224*100 = 56.47%
        assert seg.report["Profit Trades (% of total)"] == "56.47% (2950)"

    def test_loss_trades_percent_and_count(self):
        seg = _run_report(_FullStats(trades=5224, loss_trades=2274)).segments["is"]
        # 2274/5224*100 = 43.53%
        assert seg.report["Loss Trades (% of total)"] == "43.53% (2274)"

    def test_short_trades_won_percent(self):
        # §4.5: Short Trades (won %) = f"{pct:.2f}% ({n})"・pct=profit_short/short*100, n=short。
        seg = _run_report(_FullStats(short_trades=2624, profit_short_trades=1470)).segments["is"]
        # 1470/2624*100 = 56.02%
        assert seg.report["Short Trades (won %)"] == "56.02% (2624)"

    def test_long_trades_won_percent(self):
        seg = _run_report(_FullStats(long_trades=2600, profit_long_trades=1480)).segments["is"]
        # 1480/2600*100 = 56.92%
        assert seg.report["Long Trades (won %)"] == "56.92% (2600)"

    def test_largest_and_average_profit_loss_trade(self):
        seg = _run_report(_FullStats(
            max_profit_trade=50.0, average_profit_trade=28.68,
            max_loss_trade=-20.0, average_loss_trade=-32.2)).segments["is"]
        assert seg.report["Largest profit trade"] == "50"
        assert seg.report["Average profit trade"] == "28.68"
        assert seg.report["Largest loss trade"] == "-20"
        assert seg.report["Average loss trade"] == "-32.20"

    def test_max_consecutive_wins_losses_count_and_amount(self):
        # §4.5: Maximum consecutive wins ($) = f"{cnt} ({amt:.0f})"。
        seg = _run_report(_FullStats(
            max_con_wins=12, max_con_profit_trades=360.0,
            max_con_losses=9, max_con_loss_trades=-180.0)).segments["is"]
        assert seg.report["Maximum consecutive wins ($)"] == "12 (360)"
        assert seg.report["Maximum consecutive losses ($)"] == "9 (-180)"

    def test_maximal_consecutive_profit_loss_amount_and_count(self):
        # §4.5: Maximal consecutive profit (count) = f"{amt:.0f} ({cnt})"。
        seg = _run_report(_FullStats(
            con_profit_max=420.0, con_profit_max_trades=11,
            con_loss_max=-240.0, con_loss_max_trades=8)).segments["is"]
        assert seg.report["Maximal consecutive profit (count)"] == "420 (11)"
        assert seg.report["Maximal consecutive loss (count)"] == "-240 (8)"

    def test_balance_drawdown_maximal_amount_and_percent(self):
        # §4.5: Balance Drawdown Maximal = f"{amt:.0f} ({pct:.2f}%)"。
        seg = _run_report(_FullStats(
            balance_dd=2400.0, balance_dd_percent=10.5)).segments["is"]
        assert seg.report["Balance Drawdown Maximal"] == "2400 (10.50%)"

    def test_z_score_two_decimals(self):
        seg = _run_report(_FullStats(z_score=-1.34)).segments["is"]
        assert seg.report["Z-Score"] == "-1.34"

    def test_static_labels_expert_symbol_period(self):
        # §4.5: Expert=定数, Symbol=meta.symbol, Period=meta.period。
        seg = _run_report(_FullStats()).segments["is"]
        assert seg.report["Expert"] == "StopEntryProbe_EA"
        assert seg.report["Symbol"] == "JP225"
        assert seg.report["Period"] == "2026.04.01-04.14"

    def test_initial_deposit_integer_string(self):
        seg = _run_report(_FullStats(initial_deposit=10000.0)).segments["is"]
        assert seg.report["Initial Deposit"] == "10000"

    def test_missing_metrics_are_not_emitted(self):
        # §4.5 確定方針: BacktestStats 非保持の指標は report に出力しない（欠落キーは出さない）。
        seg = _run_report(_FullStats()).segments["is"]
        for k in ("GHPR", "Correlation (Profits,MFE)", "LR Correlation",
                  "Margin Level", "Ticks", "Minimal position holding time",
                  "History Quality"):
            assert k not in seg.report

    def test_profit_factor_inf_renders_as_inf_string(self):
        # §4.5 整形規約: Profit Factor の inf は文字列 "inf"（presenter _sanitize は
        # float のみ null 化・report 値は文字列のため素通し）。
        seg = _run_report(_FullStats(profit_factor=float("inf"))).segments["is"]
        assert seg.report["Profit Factor"] == "inf"

    def test_report_values_are_all_strings(self):
        # §4.5: report{<label>:<value文字列>}（JSON 契約・全値 str）。
        seg = _run_report(_FullStats()).segments["is"]
        assert all(isinstance(v, str) for v in seg.report.values())

    def test_payload_shape_other_keys_unchanged(self):
        # 形状不変: report 実体化で segments/summary/degradation/verdict 他キーは不変。
        p = _run_report(_FullStats())
        assert set(p.segments.keys()) == {"is", "oos"}
        assert set(p.summary.keys()) == {"is", "oos"}
        assert isinstance(p.degradation, dict)
        assert p.verdict.result in {"fail", "warn", "pass"}
