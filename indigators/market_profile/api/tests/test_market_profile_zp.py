"""market_profile_zp 純関数コアの単体テスト（合成データ・決定論）。"""

from __future__ import annotations

import numpy as np
import pytest

from market_profile_api.compute import market_profile_zp as zp


# --------------------------------------------------------------------------- #
# minute_close_grid
# --------------------------------------------------------------------------- #
_DAY = 1704067200  # 2024-01-01 00:00 UTC


def _tick(mod: int, sec_in_min: int = 0):
    return _DAY + mod * 60 + sec_in_min


def test_minute_close_grid_last_tick_wins_and_ffill():
    o = zp.SESSION_OPEN_MOD  # 窓起点相対（ISSUE-078: ブローカー分 60 起点）。
    secs = np.array([_tick(o, 5), _tick(o, 40), _tick(o + 2, 10)], dtype=np.int64)
    mids = np.array([100.0, 101.0, 105.0])
    out = zp.minute_close_grid(secs, mids, _DAY)
    assert out is not None
    grid, open_d = out
    assert grid.shape == (zp.G_MINUTES,)
    assert open_d == 100.0          # 窓内最初の tick
    assert grid[0] == 101.0         # 分内最後の tick が勝つ（61 分）
    assert grid[1] == 101.0         # 欠測分（62）は ffill
    assert grid[2] == 105.0
    assert grid[-1] == 105.0        # 末尾まで ffill


def test_minute_close_grid_leading_gap_uses_first_open():
    secs = np.array([_tick(100)], dtype=np.int64)
    mids = np.array([200.0])
    grid, open_d = zp.minute_close_grid(secs, mids, _DAY)
    assert open_d == 200.0
    assert grid[0] == 200.0  # 先頭欠測は当日最初の tick で埋める


def test_minute_close_grid_outside_window_none():
    secs = np.array([_tick(10), _tick(1439)], dtype=np.int64)  # 窓外のみ
    mids = np.array([1.0, 2.0])
    assert zp.minute_close_grid(secs, mids, _DAY) is None
    assert zp.minute_close_grid(np.array([], dtype=np.int64), np.array([]), _DAY) is None


# --------------------------------------------------------------------------- #
# obs_cell_counts
# --------------------------------------------------------------------------- #
def test_obs_cell_counts_and_partial_columns():
    closes = np.full(zp.G_MINUTES, 20005.0)
    closes[100:200] = 20015.0
    klo, khi = 2000, 2003
    counts = zp.obs_cell_counts(closes, klo, khi)
    assert counts.sum() == zp.G_MINUTES
    assert counts[0] == zp.G_MINUTES - 100  # 20005 → k=2000
    assert counts[1] == 100                 # 20015 → k=2001
    part = zp.obs_cell_counts(closes, klo, khi, col_lo=100, col_hi=200)
    assert part.sum() == 100 and part[1] == 100


# --------------------------------------------------------------------------- #
# build_step_matrix / null_b_moments_abs
# --------------------------------------------------------------------------- #
def _synth_mgrids(L: int, seed: int, scale=2.0):
    rng = np.random.default_rng(seed)
    opens = np.full(L, 20000.0)
    steps = rng.normal(scale=scale, size=(L, zp.G_MINUTES))
    grids = opens[:, None] + np.cumsum(steps, axis=1)
    return grids, opens


def test_step_matrix_roundtrip():
    grids, opens = _synth_mgrids(5, seed=1)
    S = zp.build_step_matrix(grids, opens)
    rebuilt = np.exp(np.log(opens)[:, None] + np.cumsum(S, axis=1))
    assert np.allclose(rebuilt, grids, rtol=1e-12)


def test_null_b_moments_basic_properties():
    grids, opens = _synth_mgrids(60, seed=2)
    S = zp.build_step_matrix(grids, opens)
    klo, khi = 1990, 2010
    rng = np.random.default_rng(7)
    mean, var = zp.null_b_moments_abs(S, 20000.0, klo, khi, rng=rng, m_reps=400)
    assert mean.shape == var.shape == (khi - klo + 1,)
    assert np.all(var >= 0)
    assert mean.sum() <= zp.G_MINUTES  # レンジ外棄却ぶんだけ総和は G 以下
    assert mean.sum() > zp.G_MINUTES * 0.5  # 大半はレンジ内（±100pt ≈ RW の到達域）


def test_null_b_moments_deterministic_and_partial_columns():
    grids, opens = _synth_mgrids(40, seed=3)
    S = zp.build_step_matrix(grids, opens)
    m1, v1 = zp.null_b_moments_abs(S, 20000.0, 1990, 2010, rng=np.random.default_rng(9), m_reps=300)
    m2, v2 = zp.null_b_moments_abs(S, 20000.0, 1990, 2010, rng=np.random.default_rng(9), m_reps=300)
    assert np.array_equal(m1, m2) and np.array_equal(v1, v2)
    # 部分カラム（前半のみ）→ 総和は必ず減る
    mp_, _ = zp.null_b_moments_abs(
        S, 20000.0, 1990, 2010, rng=np.random.default_rng(9), m_reps=300, col_hi=zp.G_MINUTES // 2
    )
    assert mp_.sum() < m1.sum()


def test_null_b_period_moments_matches_full_split():
    """周期分割の総和 = 全日モーメントの総和（同一サロゲート系列で厳密一致）。"""
    grids, opens = _synth_mgrids(30, seed=4)
    S = zp.build_step_matrix(grids, opens)
    klo, khi = 1980, 2020
    bounds = [(0, 400), (400, 900), (900, zp.G_MINUTES)]
    per = zp.null_b_period_moments(
        S, 20000.0, klo, khi, bounds, rng=np.random.default_rng(11), m_reps=200
    )
    full_mean, _ = zp.null_b_moments_abs(
        S, 20000.0, klo, khi, rng=np.random.default_rng(11), m_reps=200
    )
    stacked = np.sum([m for m, _ in per], axis=0)
    assert np.allclose(stacked, full_mean, rtol=0, atol=1e-12)


def test_day_seed_deterministic():
    assert zp.day_seed("JP225", 123) == zp.day_seed("JP225", 123)
    assert zp.day_seed("JP225", 123) != zp.day_seed("JP225", 124)


# --------------------------------------------------------------------------- #
# セッション日切り（ISSUE-078）: セッション窓はブローカー分（NY17:00 ET 基準日内オフセット）
# --------------------------------------------------------------------------- #
class TestSessionDayWindow:
    MON_START = 1783890000  # 2026-07-12 21:00 UTC（夏・月曜セッション始端）。

    def test_session_window_constants_are_broker_relative(self):
        # 実測（ISSUE-078）: オープン=ブローカー01:00（冬ちょうど・夏01:01-06）/ クローズ=23:14（夏冬同値）。
        assert zp.SESSION_OPEN_MOD == 60
        assert zp.SESSION_CLOSE_MOD == 1394
        assert zp.G_MINUTES == 1335
        assert zp.K_BRACKETS == (1394 - 60) // 30 + 1

    def test_minute_close_grid_maps_session_relative_minutes(self):
        # day_start=セッション始端。始端+60分（ブローカー01:00）のティックが grid[0] に入る。
        secs = np.array([self.MON_START + 60 * 60, self.MON_START + 61 * 60], dtype=np.int64)
        mids = np.array([100.0, 110.0])
        grid = zp.minute_close_grid(secs, mids, self.MON_START)
        assert grid is not None
        closes, open_d = grid
        assert closes.shape == (zp.G_MINUTES,)
        assert closes[0] == 100.0 and closes[1] == 110.0
        assert open_d == 100.0

    def test_minute_close_grid_excludes_outside_session_window(self):
        # 窓外（始端+10分＝ブローカー00:10 と 始端+1395分=23:15）は除外＝窓内ゼロで None。
        secs = np.array([self.MON_START + 10 * 60, self.MON_START + 1395 * 60], dtype=np.int64)
        mids = np.array([100.0, 110.0])
        assert zp.minute_close_grid(secs, mids, self.MON_START) is None

    def test_compute_walker_requests_session_windows(self, monkeypatch, tmp_path):
        # 日ウォークが [セッション始端, 翌始端) 窓で tick を読む（UTC 深夜切りでない）。
        from market_profile_api.compute import market_profile_dwell as mpd
        monkeypatch.setattr(zp, "_ZP_CACHE_ROOT", tmp_path)
        zp._reset_caches()
        windows = []

        def spy(symbol, start, end):
            windows.append((int(start), int(end)))
            return np.array([], dtype=np.int64), np.array([], dtype=np.float64)

        monkeypatch.setattr(mpd, "_load_window_ticks", spy)
        zp.compute_zp_profile(
            "JP225", self.MON_START, self.MON_START, 95.0, 115.0, 4,
            bar_sec=86400, now=self.MON_START + 3 * 86400,
        )
        assert (self.MON_START, self.MON_START + 86400) in windows
