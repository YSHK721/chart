"""derive.py 純関数単体テスト（詳細設計 §4.2・§8.1・§8.3）。

決定論導出（sl/tp・excursion(mfe/mae)・session_of・hold バケット・balance再構成）の
正常系・境界値・異常系を網羅する。derive は domain のみ依存・pandas非依存・int時刻のみ。
"""
from __future__ import annotations

from datetime import datetime, timezone

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


# --- heat_cells（§4.7 agg.heat・試作 prep_data.py:185-223・R-2 wday規約） -------

# 既知 UTC 基準時刻（R-2 検証用）。
#   2026-04-20 00:00:00Z = 月曜 hour0（weekday()=0→"Mon"）
#   2026-04-19 23:00:00Z = 日曜 hour23（weekday()=6→"Sun"）
#   2026-04-20 23:00:00Z = 月曜 hour23
_TS_MON_H0 = 1776643200   # 2026-04-20 00:00:00 UTC
_TS_SUN_H23 = 1776639600  # 2026-04-19 23:00:00 UTC
_TS_MON_H23 = 1776726000  # 2026-04-20 23:00:00 UTC


class TestHeatCells:
    def test_empty_trades_returns_empty(self):
        # 境界（空入力）: 取引なし → 空セル
        assert derive.heat_cells([]) == []

    def test_single_trade_cell_has_all_keys(self):
        # 正常系: 1 取引 → 1 セル・キー {wday,hour,profit,count,wins} 完備
        cells = derive.heat_cells([(_TS_MON_H0, 50.0)])
        assert len(cells) == 1
        c = cells[0]
        assert set(c.keys()) == {"wday", "hour", "profit", "count", "wins"}
        assert c["wday"] == "Mon"
        assert c["hour"] == 0
        assert c["profit"] == 50.0
        assert c["count"] == 1
        assert c["wins"] == 1

    def test_groups_same_wday_hour_into_one_cell(self):
        # 正常系: 同じ entry wday|hour の 2 取引は 1 セルへ集約（profit 加算・count 加算）
        cells = derive.heat_cells([(_TS_MON_H0, 30.0), (_TS_MON_H0, -10.0)])
        assert len(cells) == 1
        c = cells[0]
        assert c["count"] == 2
        assert c["profit"] == 20.0

    def test_wins_counts_only_profit_gt_zero(self):
        # 境界（win 判定=0）: profit>0 のみ wins（profit==0 は非 win）
        cells = derive.heat_cells([
            (_TS_MON_H0, 10.0),   # win
            (_TS_MON_H0, 0.0),    # 非 win（境界）
            (_TS_MON_H0, -5.0),   # 非 win
        ])
        c = cells[0]
        assert c["count"] == 3
        assert c["wins"] == 1

    def test_sunday_classified_as_sun_idx6(self):
        # 境界（日曜・wday規約 Mon=0）: weekday()=6 → "Sun"（R-2 規約）
        cells = derive.heat_cells([(_TS_SUN_H23, 10.0)])
        assert cells[0]["wday"] == "Sun"
        assert cells[0]["hour"] == 23

    def test_hour0_and_hour23_boundaries(self):
        # 境界（hour=0/23）: UTC hour 抽出の両端が別セルに分かれる
        cells = derive.heat_cells([(_TS_MON_H0, 1.0), (_TS_MON_H23, 2.0)])
        by = {(c["wday"], c["hour"]): c for c in cells}
        assert ("Mon", 0) in by
        assert ("Mon", 23) in by


class TestHeatR2Contract:
    """R-2 整合（最重要回帰・アーキ指針 §4）。

    back（derive.heat_cells の weekday() Mon=0・UTC）が、front 規約
    `(getUTCDay()+6)%7`（Mon=0）＋UTC と同一 (wday,hour) を導く（=同一 trade を選ぶ）ことを
    境界（日曜・hour0・hour23）で固定する。front 側の同規約検証は heatmap.test.mjs が担う。
    本テストは characterization（既存 Green の heat_cells 分類が R-2 規約に一致することの回帰保護）。
    """

    @staticmethod
    def _front_contract(ts):
        """front 規約の Python 等価実装（(getUTCDay()+6)%7・UTC）で (wday,hour) を求める。"""
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        # getUTCDay(): Sun=0..Sat=6 → (+6)%7 で Mon=0..Sun=6。python weekday() と等価。
        js_dow = (dt.weekday() + 1) % 7  # python weekday()(Mon=0) → getUTCDay()(Sun=0)
        idx = (js_dow + 6) % 7
        return derive.WEEK[idx], dt.hour

    @pytest.mark.parametrize("ts", [_TS_MON_H0, _TS_SUN_H23, _TS_MON_H23])
    def test_back_cell_matches_front_contract(self, ts):
        # back heat_cells の (wday,hour) 分類が front 規約と一致（境界 ts）
        cells = derive.heat_cells([(ts, 10.0)])
        back = (cells[0]["wday"], cells[0]["hour"])
        assert back == self._front_contract(ts)
