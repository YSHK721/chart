"""market_profile_dwell（adapter/compute/market_profile_dwell.py）と controller の src=dwell 検証。

対象:
  - 純ロジック（合成ティックで決定論）:
      _build_active_table / _session_dwell / compute_dwell_profile の
      dwell 集計・POC/VA・ビン整合・休場除外（薄い時間帯が 0 になる）。
  - controller: src='dwell'（既知 tick ref）で 200/profile 妥当、src='dwell'＆非 tick ref で 400、
      src 不正で 400、src 省略で candle 後方互換。
  - 統合（実データ・軽量）: jp225_tick の小窓で src=dwell が 200 かつ POC がレンジ内（perf 上限が効く）。

設計方針（AAA・handle_compute / candle テストの流儀に合わせる）:
  合成ティックは _load_window_ticks（単一注入点）を monkeypatch し、既知の (secs, mids) を注入する。
  日別ロールバック・active table・部分集計はすべて _load_window_ticks を経由するため、注入 1 点で決定論化する。
"""

from __future__ import annotations

import numpy as np
import pytest

from adapter.compute import market_profile_dwell as mpd
from adapter.controller.market_profile_controller import handle_market_profile

_DAY = 86400
# 2024-01-01 00:00 UTC（月曜・UTC 真夜中）。weekday = ((s//86400)+3)%7 = 0（月）。
_DAY0 = 1704067200
_HOT = 1000.0   # 活発時間帯に密集＝滞在が積み上がる価格。
_COLD = 1100.0  # 休場時間帯にのみ現れる価格＝滞在は除外され 0 になる。


# --------------------------------------------------------------------------- #
# テスト基盤: 合成ティック注入 / キャッシュ隔離
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_caches():
    """プロセス内キャッシュ（日別/部分/active table）をテスト間で隔離する。"""
    mpd._reset_caches()
    yield
    mpd._reset_caches()


def _make_loader(master_secs, master_mids):
    """master 配列を [start, end) にフィルタして (secs, mids) を返す _load_window_ticks 代替。"""
    s = np.asarray(master_secs, dtype=np.int64)
    m = np.asarray(master_mids, dtype=np.float64)

    def _loader(symbol, start, end):
        win = (s >= int(start)) & (s < int(end))
        s2, m2 = s[win], m[win]
        order = np.argsort(s2, kind="stable")
        return s2[order], m2[order]

    return _loader


def _synthetic_master():
    """3 日ぶんの合成ティック。活発 hr2 に HOT 密集、休場 hr20 に COLD 疎ら。"""
    secs, mids = [], []
    for d in range(3):
        base = _DAY0 + d * _DAY
        # 活発時間帯（hr2=07200..10800s）: 30 ティックを 10 秒間隔で HOT に置く。
        for i in range(30):
            secs.append(base + 7200 + 10 * i)
            mids.append(_HOT)
        # 休場時間帯（hr20=72000..75600s）: 600 秒ギャップの 2 ティックを COLD に置く。
        secs.append(base + 72000)
        mids.append(_COLD)
        secs.append(base + 72600)
        mids.append(_COLD)
    return secs, mids


# --------------------------------------------------------------------------- #
# 純ロジック: _build_active_table
# --------------------------------------------------------------------------- #
class TestBuildActiveTable:
    def test_dense_bucket_active_sparse_bucket_inactive(self):
        # Arrange: (月, hr2) に 30 ティック、(月, hr20) に 2 ティック。
        secs, _ = _synthetic_master()
        s = np.asarray(secs, dtype=np.int64)

        # Act
        table = mpd._build_active_table(s)

        # Assert
        assert table.shape == (7, 24)
        assert table.dtype == np.bool_
        assert bool(table[0, 2]) is True     # 密集バケット＝活発。
        assert bool(table[0, 20]) is False   # 疎バケット（2 < max*0.10=3）＝休場。
        assert bool(table[0, 12]) is False   # ティック皆無＝休場。


# --------------------------------------------------------------------------- #
# 純ロジック: _session_dwell（活発秒の積分・休場 0・時間境界跨ぎ）
# --------------------------------------------------------------------------- #
class TestSessionDwell:
    def test_active_hour_gaps_are_counted(self):
        # Arrange: (月, hr2) のみ活発。同一活発時間内の 2 ギャップ。
        table = np.zeros((7, 24), dtype=bool)
        table[0, 2] = True
        base = _DAY0 + 7200
        secs = np.array([base, base + 30, base + 100], dtype=np.int64)

        # Act
        dwell = mpd._session_dwell(secs, table)

        # Assert: 活発時間内のギャップは満額計上（len=len(secs)-1）。
        assert list(np.round(dwell, 6)) == [30.0, 70.0]

    def test_closed_hour_gaps_are_zero(self):
        # Arrange: 全休場テーブル。
        table = np.zeros((7, 24), dtype=bool)
        base = _DAY0 + 7200
        secs = np.array([base, base + 30, base + 100], dtype=np.int64)

        # Act
        dwell = mpd._session_dwell(secs, table)

        # Assert: 休場帯の滞在は 0。
        assert list(dwell) == [0.0, 0.0]

    def test_hour_boundary_crossing_integrates_active_seconds(self):
        # Arrange: hr2 のみ活発。hr2→hr3（休場）を跨ぐギャップ。
        table = np.zeros((7, 24), dtype=bool)
        table[0, 2] = True
        secs = np.array([_DAY0 + 7200, _DAY0 + 10700, _DAY0 + 11000], dtype=np.int64)

        # Act
        dwell = mpd._session_dwell(secs, table)

        # Assert: 同一 hr2 内=3500、跨ぎは hr2 側 100 秒のみ活発、hr3 側は 0。
        assert list(np.round(dwell, 6)) == [3500.0, 100.0]


# --------------------------------------------------------------------------- #
# 純ロジック: compute_dwell_profile（窓合算→表示 bin・POC/VA・休場除外）
# --------------------------------------------------------------------------- #
class TestComputeDwellProfile:
    def _run(self, monkeypatch):
        secs, mids = _synthetic_master()
        monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
        return mpd.compute_dwell_profile(
            "JP225", _DAY0, _DAY0 + 2 * _DAY, 990.0, 1110.0, 12, va_pct=0.70, bar_sec=_DAY
        )

    def test_schema_keys_match_candle(self, monkeypatch):
        profile = self._run(monkeypatch)
        assert set(profile) == {
            "bins", "poc", "va_low", "va_high",
            "price_min", "price_max", "tpo_units", "n_bins",
        }
        assert profile["n_bins"] == 12
        assert len(profile["bins"]) == 12
        assert set(profile["bins"][0]) == {"price", "tpo", "norm"}

    def test_poc_is_hot_price(self, monkeypatch):
        # HOT=1000 に滞在が集中 → POC は HOT のビン中心（1005）。
        profile = self._run(monkeypatch)
        assert profile["poc"] == 1005.0
        assert profile["price_min"] <= profile["poc"] <= profile["price_max"]
        assert profile["price_min"] <= profile["va_low"] <= profile["va_high"] <= profile["price_max"]

    def test_closed_session_price_excluded(self, monkeypatch):
        # COLD=1100 は休場帯にのみ出現 → その滞在は 0（除外）。
        profile = self._run(monkeypatch)
        cold_bin = next(b for b in profile["bins"] if b["price"] == 1105.0)
        assert cold_bin["tpo"] == 0

    def test_tpo_units_are_positive_dwell_seconds(self, monkeypatch):
        # tpo_units = 総 dwell 秒（int へ丸め）。HOT の活発滞在が積み上がる。
        profile = self._run(monkeypatch)
        assert profile["tpo_units"] > 0

    def test_perf_cap_limits_scanned_days(self, monkeypatch):
        # 巨大窓要求でも直近 _MAX_DWELL_DAYS 日ぶんに限定して走査する（呼び出し窓を記録）。
        secs, mids = _synthetic_master()
        calls = []
        base_loader = _make_loader(secs, mids)

        def _spy(symbol, start, end):
            calls.append((int(start), int(end)))
            return base_loader(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", _spy)
        # t0 を大昔に置く（_MAX_DWELL_DAYS を大きく超える窓）。
        t1 = _DAY0 + 2 * _DAY
        far_t0 = t1 - 5000 * _DAY
        mpd.compute_dwell_profile("JP225", far_t0, t1, 990.0, 1110.0, 12, bar_sec=_DAY)
        # 走査開始は win_to - _MAX_DWELL_DAYS*_DAY 以降に丸められる（far_t0 まで遡らない）。
        win_to = t1 + _DAY
        cap_from = win_to - mpd._MAX_DWELL_DAYS * _DAY
        earliest = min(s for s, _ in calls)
        assert earliest >= cap_from


# --------------------------------------------------------------------------- #
# Y2a: 当日（未確定 UTC 日）をキャッシュしない（完了日のみキャッシュ）
# --------------------------------------------------------------------------- #
class TestDayRollupCaching:
    def _spy_loader(self, monkeypatch):
        secs, mids = _synthetic_master()
        calls = []
        base = _make_loader(secs, mids)

        def _spy(symbol, start, end):
            calls.append((int(start), int(end)))
            return base(symbol, start, end)

        monkeypatch.setattr(mpd, "_load_window_ticks", _spy)
        return calls

    def test_completed_day_is_cached(self, monkeypatch):
        # Arrange: 完了日（day_start+86400 <= now）。
        calls = self._spy_loader(monkeypatch)
        table = np.ones((7, 24), dtype=bool)
        day = _DAY0
        now = day + _DAY + 1  # 日は完了済み。

        # Act: 同一日を 2 回呼ぶ。
        mpd._day_rollup("JP225", day, table, now)
        mpd._day_rollup("JP225", day, table, now)

        # Assert: 2 回目はキャッシュ命中＝loader は 1 回のみ。キャッシュに登録される。
        assert len(calls) == 1
        assert ("JP225", day) in mpd._DAY_CACHE

    def test_incomplete_day_is_not_cached(self, monkeypatch):
        # Arrange: 未完了日（day_start+86400 > now）＝現在進行中の当日。
        calls = self._spy_loader(monkeypatch)
        table = np.ones((7, 24), dtype=bool)
        day = _DAY0
        now = day + 100  # now < day+86400 → 日は未完了。

        # Act: 同一日を 2 回呼ぶ。
        mpd._day_rollup("JP225", day, table, now)
        mpd._day_rollup("JP225", day, table, now)

        # Assert: 未完了日は毎回再計算＝loader は 2 回。キャッシュに登録されない。
        assert len(calls) == 2
        assert ("JP225", day) not in mpd._DAY_CACHE

    def test_incomplete_partial_is_not_cached(self, monkeypatch):
        # Arrange: 当日の部分足（hi > now）。
        calls = self._spy_loader(monkeypatch)
        table = np.ones((7, 24), dtype=bool)
        lo = _DAY0
        hi = _DAY0 + 3600
        now = lo + 100  # now < hi → 部分窓は未完了。

        # Act
        mpd._partial_rollup("JP225", lo, hi, table, now)
        mpd._partial_rollup("JP225", lo, hi, table, now)

        # Assert: 未完了の部分窓は毎回再計算＝loader は 2 回。キャッシュに登録されない。
        assert len(calls) == 2
        assert ("JP225", lo, hi) not in mpd._PARTIAL_CACHE


# --------------------------------------------------------------------------- #
# controller: src 分岐
# --------------------------------------------------------------------------- #
def _patch_dwell_data(monkeypatch):
    """dwell 経路の load_candles（価格レンジ/時刻）と _load_window_ticks（滞在）を注入する。"""
    secs, mids = _synthetic_master()
    monkeypatch.setattr(mpd, "_load_window_ticks", _make_loader(secs, mids))
    candles = [
        {"time": _DAY0, "open": 1000, "high": 1110, "low": 990, "close": 1005},
        {"time": _DAY0 + _DAY, "open": 1005, "high": 1108, "low": 992, "close": 1002},
        {"time": _DAY0 + 2 * _DAY, "open": 1002, "high": 1106, "low": 991, "close": 1000},
    ]
    import adapter.controller.market_profile_controller as ctrl
    monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)


class TestControllerDwell:
    def test_dwell_known_tick_ref_returns_200_valid(self, monkeypatch):
        # Arrange
        _patch_dwell_data(monkeypatch)

        # Act
        status, payload = handle_market_profile("jp225_tick", timeframe="1D", src="dwell")

        # Assert
        assert status == 200
        assert payload["ok"] is True
        assert payload["src"] == "dwell"
        assert "atom" in payload
        profile = payload["profile"]
        assert set(profile) >= {
            "bins", "poc", "va_low", "va_high",
            "price_min", "price_max", "tpo_units", "n_bins",
        }
        assert profile["price_min"] <= profile["poc"] <= profile["price_max"]

    def test_dwell_non_tick_ref_returns_400(self, monkeypatch):
        # 'jp225'（既知だが非 tick）で src=dwell → 400 validation。
        status, payload = handle_market_profile("jp225", src="dwell")
        assert status == 400
        assert payload["error"]["type"] == "validation"

    def test_invalid_src_returns_400(self):
        status, payload = handle_market_profile("sample", src="bogus")
        assert status == 400
        assert payload["error"]["type"] == "validation"

    def test_src_omitted_is_candle_backward_compatible(self):
        # src 省略 → 既存 candle 経路（後方互換）。
        status, payload = handle_market_profile("sample")
        assert status == 200
        assert payload["ok"] is True
        assert payload.get("src") == "candle"
        profile = payload["profile"]
        assert profile["n_bins"] == 60
        assert len(profile["bins"]) == 60


# --------------------------------------------------------------------------- #
# controller: Y1 — dwell の価格レンジを集計窓（直近 _MAX_DWELL_DAYS 日）に揃える
# --------------------------------------------------------------------------- #
def _empty_loader(symbol, start, end):
    """dwell 集計を空にする _load_window_ticks 代替（レンジ算出のみを検証するため）。"""
    return mpd._EMPTY_SECS, mpd._EMPTY_MIDS


class TestControllerDwellRangeCap:
    def _candles_spanning_cap(self):
        """cap（直近 _MAX_DWELL_DAYS 日）を超える candle 集合。

        古い期間（cap 外）に極端 low/high、直近 250 日以内に穏当 low/high を置く。
        """
        cap = mpd._MAX_DWELL_DAYS
        t1 = _DAY0 + 500 * _DAY
        candles = [
            # cap 外（time < t1 - cap*_DAY）: 極端な安値/高値。除外されるべき。
            {"time": t1 - 400 * _DAY, "open": 500, "high": 9999.0, "low": 1.0, "close": 500},
            {"time": t1 - 300 * _DAY, "open": 500, "high": 8000.0, "low": 5.0, "close": 500},
        ]
        # cap 以内（time >= t1 - cap*_DAY）: 穏当なレンジ 990..1110。これがレンジを定義する。
        for k in range(200, -1, -1):
            candles.append(
                {"time": t1 - k * _DAY, "open": 1000, "high": 1110.0, "low": 990.0, "close": 1005}
            )
        return candles

    def test_dwell_price_range_uses_recent_max_dwell_days_only(self, monkeypatch):
        # Arrange: cap を超える期間の candle。古い極値は cap 外に、穏当レンジは cap 以内に置く。
        import adapter.controller.market_profile_controller as ctrl

        candles = self._candles_spanning_cap()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)
        monkeypatch.setattr(mpd, "_load_window_ticks", _empty_loader)

        # Act
        status, payload = handle_market_profile("jp225_tick", timeframe="1D", src="dwell")

        # Assert: レンジ = 直近 250 日ぶんの low/high（990/1110）。全期間の極値(1/9999)には引きずられない。
        assert status == 200
        p = payload["profile"]
        assert p["price_min"] == 990.0
        assert p["price_max"] == 1110.0

    def test_candle_path_price_range_unchanged_by_dwell_fix(self, monkeypatch):
        # candle 経路（src 省略）は全 candle の low/high をそのまま使う（Y1 修正の影響を受けない）。
        import adapter.controller.market_profile_controller as ctrl

        candles = self._candles_spanning_cap()
        monkeypatch.setattr(ctrl.dataset, "load_candles", lambda ref, tf, limit: candles)

        status, payload = handle_market_profile("sample", timeframe="1D")

        assert status == 200
        assert payload["src"] == "candle"
        p = payload["profile"]
        # candle 経路は全期間の極値をそのまま反映（cap を適用しない）。
        assert p["price_min"] == 1.0
        assert p["price_max"] == 9999.0


# --------------------------------------------------------------------------- #
# 統合（実データ・軽量）: jp225_tick 小窓で src=dwell が 200 / POC レンジ内
# --------------------------------------------------------------------------- #
def _tick_data_available() -> bool:
    from adapter.compute import dataset
    try:
        return dataset.DATASET_WHITELIST["jp225_tick"].is_file()
    except Exception:
        return False


@pytest.mark.slow
@pytest.mark.skipif(not _tick_data_available(), reason="jp225_tick 実データが無い環境ではスキップ")
def test_integration_dwell_small_window_real_data():
    status, payload = handle_market_profile(
        "jp225_tick", timeframe="1D", limit="20", bins="30", src="dwell"
    )
    assert status == 200
    assert payload["ok"] is True
    assert payload["src"] == "dwell"
    p = payload["profile"]
    assert p["n_bins"] == 30
    assert p["price_min"] <= p["poc"] <= p["price_max"]
