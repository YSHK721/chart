"""market_profile 純パラメータ移植（試作 prototype_260630-01）の検証。

対象（本ファイルで新規追加する2機能。既存 candle/dwell 挙動は不変）:
  ① src=m1（tick数・metric='count'）:
      compute_dwell_profile(metric='count') が dwell 秒でなく生ティック数（cnt[]）で集計し、
      セッションマスクを適用しない（休場帯の価格もカウントされ、dwell とは分布が異なる）ことを検証。
      controller: src='m1' で 200・atom='tick数'、非 tick ref は 400。
  ② range（レンジpt・barw）:
      barw 指定時 n_bins = round((price_max-price_min)/barw) が bins に優先（candle/dwell 両方）。
      auto/未指定は従来 bins。応答トップレベルに実効 bar_width（pt）を含む。

設計方針（AAA・test_market_profile_dwell.py の流儀に合わせる）:
  合成ティックは _load_window_ticks（単一注入点）を monkeypatch して決定論化する。
  controller の barw 検証は load_candles を monkeypatch して価格レンジを固定する。
"""

from __future__ import annotations

import numpy as np
import pytest

from market_profile_api.compute import market_profile_dwell as mpd
from market_profile_api.controller.market_profile_controller import handle_market_profile

_DAY = 86400
# 2024-01-01 00:00 UTC（月曜・UTC 真夜中）。weekday = ((s//86400)+3)%7 = 0（月）。
_DAY0 = 1704067200
_HOT = 1000.0   # 活発時間帯に密集。
_COLD = 1100.0  # 休場時間帯にのみ現れる価格（dwell では 0・count では加算される）。


@pytest.fixture(autouse=True)
def _isolate_caches(tmp_path, monkeypatch):
    # ディスクキャッシュ基点を tmp へ差し替え、実データ DATA_DIR/cache への書込を防ぐ。
    monkeypatch.setattr(mpd, "_CACHE_ROOT", tmp_path / "mp_dwell_cache")
    mpd._reset_caches()
    yield
    mpd._reset_caches()


def _make_loader(master_secs, master_mids):
    s = np.asarray(master_secs, dtype=np.int64)
    m = np.asarray(master_mids, dtype=np.float64)

    def _loader(symbol, start, end):
        win = (s >= int(start)) & (s < int(end))
        s2, m2 = s[win], m[win]
        order = np.argsort(s2, kind="stable")
        return s2[order], m2[order]

    return _loader


def _synthetic_master():
    """3 日ぶん。活発 hr2 に HOT 30 ティック/日、休場 hr20 に COLD 2 ティック/日。"""
    secs, mids = [], []
    for d in range(3):
        base = _DAY0 + d * _DAY
        for i in range(30):
            secs.append(base + 7200 + 10 * i)
            mids.append(_HOT)
        secs.append(base + 72000)
        mids.append(_COLD)
        secs.append(base + 72600)
        mids.append(_COLD)
    return secs, mids


# --------------------------------------------------------------------------- #
# ① compute_dwell_profile(metric='count') — 生ティック数・セッション非適用
# --------------------------------------------------------------------------- #
class TestComputeCountProfile:
    def _run(self, monkeypatch, metric):
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        # ISSUE-089: 表は旧セマンティクス（窓ティック由来）を固定（表仕様の検証は dwell テスト側）。
        table = mpd._build_active_table(np.asarray(secs, dtype=np.int64))
        monkeypatch.setattr(mpd, "_table_for_day", lambda _s, _d: table)
        return mpd.compute_dwell_profile(
            "JP225", _DAY0, _DAY0 + 2 * _DAY, 990.0, 1110.0, 12,
            va_pct=0.70, bar_sec=_DAY, metric=metric,
        )

    def test_schema_keys_match_candle(self, monkeypatch):
        # metric='count' でも応答スキーマ（8キー）は不変。
        profile = self._run(monkeypatch, "count")
        assert set(profile) == {
            "bins", "poc", "va_low", "va_high",
            "price_min", "price_max", "tpo_units", "n_bins",
        }

    def test_tpo_units_is_total_tick_count(self, monkeypatch):
        # 3 日 × (30 HOT + 2 COLD) = 96 ティック。tpo_units = 総ティック数。
        profile = self._run(monkeypatch, "count")
        assert profile["tpo_units"] == 96

    def test_poc_is_hot_price(self, monkeypatch):
        # HOT に 90 ティック（COLD 6）→ POC は HOT ビン中心（1005）。
        profile = self._run(monkeypatch, "count")
        assert profile["poc"] == 1005.0

    def test_closed_session_price_is_counted(self, monkeypatch):
        # COLD=1100（休場帯）も count では加算される（セッションマスク非適用）。
        profile = self._run(monkeypatch, "count")
        cold_bin = next(b for b in profile["bins"] if b["price"] == 1105.0)
        assert cold_bin["tpo"] == 6

    def test_count_differs_from_dwell_at_closed_session(self, monkeypatch):
        # 同一ティックでも dwell は休場帯を 0 に、count は加算 → 分布が異なる。
        count_p = self._run(monkeypatch, "count")
        mpd._reset_caches()
        dwell_p = self._run(monkeypatch, "dwell")
        cold_count = next(b for b in count_p["bins"] if b["price"] == 1105.0)["tpo"]
        cold_dwell = next(b for b in dwell_p["bins"] if b["price"] == 1105.0)["tpo"]
        assert cold_count > 0
        assert cold_dwell == 0

    def test_default_metric_is_dwell_backward_compatible(self, monkeypatch):
        # metric 省略時は dwell（既存挙動）。COLD は 0。
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        # ISSUE-089: 表は旧セマンティクス（窓ティック由来）を固定。
        table = mpd._build_active_table(np.asarray(secs, dtype=np.int64))
        monkeypatch.setattr(mpd, "_table_for_day", lambda _s, _d: table)
        profile = mpd.compute_dwell_profile(
            "JP225", _DAY0, _DAY0 + 2 * _DAY, 990.0, 1110.0, 12, bar_sec=_DAY
        )
        cold_bin = next(b for b in profile["bins"] if b["price"] == 1105.0)
        assert cold_bin["tpo"] == 0


# --------------------------------------------------------------------------- #
# ① controller: src='m1'
# --------------------------------------------------------------------------- #
def _patch_dwell_data(monkeypatch):
    secs, mids = _synthetic_master()
    monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
    candles = [
        {"time": _DAY0, "open": 1000, "high": 1110, "low": 990, "close": 1005},
        {"time": _DAY0 + _DAY, "open": 1005, "high": 1108, "low": 992, "close": 1002},
        {"time": _DAY0 + 2 * _DAY, "open": 1002, "high": 1106, "low": 991, "close": 1000},
    ]
    import market_profile_api.controller.market_profile_controller as ctrl
    monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)


class TestControllerM1:
    def test_m1_known_tick_ref_returns_200_atom_tickcount(self, monkeypatch):
        _patch_dwell_data(monkeypatch)
        status, payload = handle_market_profile("jp225_tick", timeframe="1D", src="m1")
        assert status == 200
        assert payload["ok"] is True
        assert payload["src"] == "m1"
        assert payload["atom"] == "tick数"
        p = payload["profile"]
        assert set(p) >= {
            "bins", "poc", "va_low", "va_high",
            "price_min", "price_max", "tpo_units", "n_bins",
        }
        assert p["price_min"] <= p["poc"] <= p["price_max"]

    def test_m1_non_tick_ref_returns_400(self):
        status, payload = handle_market_profile("jp225", src="m1")
        assert status == 400
        assert payload["error"]["type"] == "validation"

    def test_dwell_atom_unchanged(self, monkeypatch):
        # src='dwell' の atom は従来（セッション認識）のまま（不変）。
        _patch_dwell_data(monkeypatch)
        status, payload = handle_market_profile("jp225_tick", timeframe="1D", src="dwell")
        assert status == 200
        assert payload["src"] == "dwell"
        assert "セッション" in payload["atom"]


# --------------------------------------------------------------------------- #
# 修正1: 未知 src の 400 メッセージが実許可値（m1 を含む）と整合すること（回帰）
# --------------------------------------------------------------------------- #
class TestUnknownSrcMessage:
    def test_unknown_src_400_message_lists_m1(self):
        # 未知 src の 400 メッセージは実許可値 _ALLOWED_SRC を列挙する（m1 を含む）。
        status, payload = handle_market_profile("sample", src="bogus")
        assert status == 400
        assert payload["error"]["type"] == "validation"
        assert "m1" in payload["error"]["message"]

    def test_unknown_src_400_message_lists_all_allowed_src(self):
        # candle/dwell/m1 すべてが単一情報源から列挙される（サイレント乖離防止）。
        status, payload = handle_market_profile("sample", src="bogus")
        assert status == 400
        msg = payload["error"]["message"]
        for allowed in ("candle", "dwell", "m1"):
            assert allowed in msg


# --------------------------------------------------------------------------- #
# ② barw（レンジpt）→ n_bins 優先 + bar_width 応答
# --------------------------------------------------------------------------- #
def _patch_candles(monkeypatch, low, high, n=3):
    candles = [
        {"time": _DAY0 + i * _DAY, "open": low, "high": high, "low": low, "close": high}
        for i in range(n)
    ]
    import market_profile_api.controller.market_profile_controller as ctrl
    monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
    return candles


class TestBarwCandle:
    def test_barw_overrides_bins_25pt(self, monkeypatch):
        # span=100, barw=25 → n_bins=round(100/25)=4、bar_width=25.0。
        _patch_candles(monkeypatch, 1000.0, 1100.0)
        status, payload = handle_market_profile("sample", timeframe="1D", bins="60", barw="25")
        assert status == 200
        assert payload["profile"]["n_bins"] == 4
        assert payload["bar_width"] == 25.0

    def test_barw_overrides_bins_50pt(self, monkeypatch):
        _patch_candles(monkeypatch, 1000.0, 1100.0)
        status, payload = handle_market_profile("sample", timeframe="1D", barw="50")
        assert status == 200
        assert payload["profile"]["n_bins"] == 2
        assert payload["bar_width"] == 50.0

    def test_barw_auto_uses_bins(self, monkeypatch):
        # barw 未指定 → 従来 bins（既定 60）。bar_width は実効幅で応答に含む。
        _patch_candles(monkeypatch, 1000.0, 1100.0)
        status, payload = handle_market_profile("sample", timeframe="1D")
        assert status == 200
        assert payload["profile"]["n_bins"] == 60
        assert "bar_width" in payload

    def test_barw_zero_uses_bins(self, monkeypatch):
        _patch_candles(monkeypatch, 1000.0, 1100.0)
        status, payload = handle_market_profile("sample", timeframe="1D", bins="24", barw="0")
        assert status == 200
        assert payload["profile"]["n_bins"] == 24

    def test_barw_tiny_clamped_to_max_bins(self, monkeypatch):
        # 極小 barw で n_bins が [1, _MAX_BINS] にクランプされる（占有防止・既存クランプ流用）。
        _patch_candles(monkeypatch, 1000.0, 1100.0)
        status, payload = handle_market_profile("sample", timeframe="1D", barw="0.01")
        assert status == 200
        assert payload["profile"]["n_bins"] == 1000


class TestBarwDwell:
    def test_barw_overrides_bins_dwell(self, monkeypatch):
        # dwell 経路の price レンジ 990..1110（span 120）。barw=30 → n_bins=4。
        _patch_dwell_data(monkeypatch)
        status, payload = handle_market_profile(
            "jp225_tick", timeframe="1D", bins="60", barw="30", src="dwell"
        )
        assert status == 200
        assert payload["profile"]["n_bins"] == 4
        assert payload["bar_width"] == 30.0

    def test_barw_overrides_bins_m1(self, monkeypatch):
        _patch_dwell_data(monkeypatch)
        status, payload = handle_market_profile(
            "jp225_tick", timeframe="1D", barw="30", src="m1"
        )
        assert status == 200
        assert payload["profile"]["n_bins"] == 4


# --------------------------------------------------------------------------- #
# 修正4: barw 境界回帰網（既存の _parse_float 吸収・_resolve_n_bins クランプを固定）
#   ※ 実装済み挙動に対する回帰テスト（Red 駆動ではない・将来のサイレント退行を禁止する）。
# --------------------------------------------------------------------------- #
class TestBarwBoundaryRegression:
    def test_barw_larger_than_span_clamps_to_one_bin(self, monkeypatch):
        # span=100（1000..1100）、barw=500 → round(100/500)=0 → 下限クランプ n_bins=1。
        _patch_candles(monkeypatch, 1000.0, 1100.0)
        status, payload = handle_market_profile("sample", timeframe="1D", barw="500")
        assert status == 200
        assert payload["profile"]["n_bins"] == 1

    def test_barw_negative_falls_back_to_auto_bins(self, monkeypatch):
        # 負の barw は _parse_float 経路で auto（0）へ丸められ、既定 bins が使われる（400 にしない）。
        _patch_candles(monkeypatch, 1000.0, 1100.0)
        status, payload = handle_market_profile("sample", timeframe="1D", bins="24", barw="-5")
        assert status == 200
        assert payload["profile"]["n_bins"] == 24

    def test_barw_non_numeric_falls_back_to_auto_bins(self, monkeypatch):
        # 非数文字列 barw も _parse_float が default(0)=auto に吸収する（400 にしない）。
        _patch_candles(monkeypatch, 1000.0, 1100.0)
        status, payload = handle_market_profile("sample", timeframe="1D", bins="24", barw="abc")
        assert status == 200
        assert payload["profile"]["n_bins"] == 24

    def test_barw_with_degenerate_range_is_safe(self, monkeypatch):
        # レンジ縮退（price_max<=price_min）＋barw 指定でも例外にならず安全な n_bins（>=1）。
        _patch_candles(monkeypatch, 1000.0, 1000.0)
        status, payload = handle_market_profile("sample", timeframe="1D", barw="30")
        assert status == 200
        assert payload["profile"]["n_bins"] >= 1
