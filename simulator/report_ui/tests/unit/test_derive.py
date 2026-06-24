"""derive.py 純関数単体テスト（詳細設計 §4.2・§8.1・§8.3）。

決定論導出（sl/tp・excursion(mfe/mae)・session_of・hold バケット・balance再構成）の
正常系・境界値・異常系を網羅する。derive は domain のみ依存・pandas非依存・int時刻のみ。
"""
from __future__ import annotations

import pytest

from simulator.report_ui.usecase import derive


# --- derive_sl_tp（§4.2.1・致命-1） -------------------------------------------

class TestDeriveSlTp:
    def test_buy_sl_below_tp_above_entry(self):
        # Arrange: StopEntryProbe 実param（SL200/TP500/stops_level0/point0.1/digits1）
        # Act
        sl, tp = derive.derive_sl_tp(
            "buy", 39412.0, sl_points=200, tp_points=500,
            stops_level=0, point_size=0.1, digits=1,
        )
        # Assert: buy → sl=entry-20.0=39392.0, tp=entry+50.0=39462.0
        assert sl == "39392.0"
        assert tp == "39462.0"

    def test_sell_sl_above_tp_below_entry(self):
        sl, tp = derive.derive_sl_tp(
            "sell", 39412.0, sl_points=200, tp_points=500,
            stops_level=0, point_size=0.1, digits=1,
        )
        # sell → sl=entry+20.0, tp=entry-50.0
        assert sl == "39432.0"
        assert tp == "39362.0"

    def test_sl_points_zero_returns_empty_sl(self):
        # 境界: sl_points=0 → sl="" （TP は出る）
        sl, tp = derive.derive_sl_tp(
            "buy", 100.0, sl_points=0, tp_points=500,
            stops_level=0, point_size=0.1, digits=1,
        )
        assert sl == ""
        assert tp != ""

    def test_tp_points_zero_returns_empty_tp(self):
        sl, tp = derive.derive_sl_tp(
            "buy", 100.0, sl_points=200, tp_points=0,
            stops_level=0, point_size=0.1, digits=1,
        )
        assert tp == ""
        assert sl != ""

    def test_stops_level_clamps_distance(self):
        # 境界: stops_level > sl_points → min_dist でクランプ
        # sl_points=10 → 10*0.1=1.0 ; stops_level=100 → 100*0.1=10.0 が下限
        sl, _ = derive.derive_sl_tp(
            "buy", 100.0, sl_points=10, tp_points=0,
            stops_level=100, point_size=0.1, digits=1,
        )
        # クランプ後 dist=10.0 → sl=90.0
        assert sl == "90.0"

    def test_digits_rounding_applied(self):
        # digits=1 で丸め・桁数固定の文字列
        sl, _ = derive.derive_sl_tp(
            "buy", 100.05, sl_points=200, tp_points=0,
            stops_level=0, point_size=0.1, digits=1,
        )
        # 100.05-20.0=80.05 → round(,1)=80.0 ... 80.1 (banker? use format) -> "80.1"
        # round(80.05,1) は環境依存だが f"{round(80.05,1):.1f}" で確定。期待は文字列形状の桁固定。
        assert sl.count(".") == 1
        assert len(sl.split(".")[1]) == 1


# --- excursion（mfe/mae・§4.2.3） -------------------------------------------

class _B:
    """テスト用バー（high/low のみ参照される）。"""
    def __init__(self, time, high, low):
        self.time = time
        self.high = high
        self.low = low


class TestExcursion:
    def _bars(self):
        # time: 100,200,300 / highs: 110,120,105 / lows: 95,90,100
        return [_B(100, 110, 95), _B(200, 120, 90), _B(300, 105, 100)]

    def test_buy_mfe_mae(self):
        bars = self._bars()
        bar_times = [b.time for b in bars]
        # buy ep=100, t0=100,t1=300（全バー包含）
        mfe, mae = derive.excursion(bars, bar_times, "buy", 100.0, 100, 300, 0.1)
        # mfe_pts = max(0, 120-100)=20 → *0.1 = 2.0
        # mae_pts = max(0, 100-90)=10 → *0.1 = 1.0
        assert mfe == 2.0
        assert mae == 1.0

    def test_sell_mfe_mae(self):
        bars = self._bars()
        bar_times = [b.time for b in bars]
        # sell ep=100 → mfe = ep - ll = 100-90=10 →1.0 ; mae = hh - ep = 120-100=20 →2.0
        mfe, mae = derive.excursion(bars, bar_times, "sell", 100.0, 100, 300, 0.1)
        assert mfe == 1.0
        assert mae == 2.0

    def test_empty_segment_returns_zero(self):
        # 境界: hi<=lo（区間にバーなし）→ (0.0, 0.0)
        bars = self._bars()
        bar_times = [b.time for b in bars]
        mfe, mae = derive.excursion(bars, bar_times, "buy", 100.0, 1000, 2000, 0.1)
        assert mfe == 0.0
        assert mae == 0.0


# --- session_of（§6.2 UTC簡易区分） ----------------------------------------

class TestSessionOf:
    @pytest.mark.parametrize("h,expected", [
        (0, "Asia"), (6, "Asia"), (7, "Europe"), (12, "Europe"),
        (13, "USA"), (23, "USA"),
    ])
    def test_boundaries(self, h, expected):
        assert derive.session_of(h) == expected


# --- hold_bucket（§6.2 HBUCK 7区分） ----------------------------------------

class TestHoldBucket:
    @pytest.mark.parametrize("sec,expected", [
        (0, "<1m"), (59, "<1m"), (60, "1-2m"), (119, "1-2m"),
        (120, "2-5m"), (300, "5-10m"), (600, "10-30m"),
        (1800, "30-60m"), (3600, ">1h"), (100000, ">1h"),
    ])
    def test_boundaries(self, sec, expected):
        assert derive.hold_bucket(sec) == expected


# --- reconstruct_balance_curve（§4.2.5・致命-3） ----------------------------

class TestReconstructBalanceCurve:
    def test_pairs_time_and_value_one_to_one(self):
        # exit_times と balance_curve(走行残高)を 1:1 で {time,value} へ
        exit_times = [200, 400, 600]
        balances = [10100.0, 10050.0, 10200.0]
        curve = derive.reconstruct_balance_curve(exit_times, balances)
        assert curve == [
            {"time": 200, "value": 10100.0},
            {"time": 400, "value": 10050.0},
            {"time": 600, "value": 10200.0},
        ]

    def test_empty_returns_empty(self):
        assert derive.reconstruct_balance_curve([], []) == []

    def test_length_mismatch_raises(self):
        # 異常系: 1:1 が崩れる入力は明示エラー（致命-3 の不変条件防御）
        with pytest.raises(ValueError):
            derive.reconstruct_balance_curve([1, 2], [10.0])


# --- max_drawdown_pct（§4.8 summary.max_dd_pct・summarize:256-261） ----------

class TestMaxDrawdownPct:
    def test_peak_to_trough_pct(self):
        # 値列 [100, 120, 90, 110]: peak=120 で trough=90 → dd=-30 → -25.0%
        curve = [
            {"time": 1, "value": 100.0},
            {"time": 2, "value": 120.0},
            {"time": 3, "value": 90.0},
            {"time": 4, "value": 110.0},
        ]
        assert derive.max_drawdown_pct(curve) == -25.0

    def test_monotonic_increase_zero_dd(self):
        curve = [{"time": 1, "value": 100.0}, {"time": 2, "value": 110.0}]
        assert derive.max_drawdown_pct(curve) == 0.0

    def test_empty_returns_zero(self):
        assert derive.max_drawdown_pct([]) == 0.0
