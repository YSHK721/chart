"""ZpStore と日別 z ロールアップ／compute_zp_profile のキャッシュ規約テスト（合成ティック）。"""

from __future__ import annotations

import numpy as np
import pytest

from market_profile_api.compute import market_profile_zp as zp

_DAY0 = 1704067200  # 2024-01-01 00:00 UTC（合成日列の起点）


def _synth_ticks_for_day(day_start: int) -> "tuple[np.ndarray, np.ndarray]":
    """day 決定論の 1 分 1 tick 合成ティック（セッション窓内・RW mid）。"""
    rng = np.random.default_rng((day_start // 86400) % 100000)
    mods = np.arange(zp.SESSION_OPEN_MOD, zp.SESSION_CLOSE_MOD + 1)
    secs = day_start + mods * 60 + 30
    mids = 20000.0 + np.cumsum(rng.normal(scale=2.0, size=mods.size))
    return secs.astype(np.int64), mids


@pytest.fixture()
def zp_env(monkeypatch, tmp_path):
    """tmp キャッシュ root・合成ティック・短縮パラメータを注入し、カウンタを返す。"""
    calls = {"load": 0}

    def fake_load(symbol, start, end):
        calls["load"] += 1
        s, e = int(start), int(end)
        all_secs, all_mids = [], []
        day = (s // 86400) * 86400
        while day < e:
            secs, mids = _synth_ticks_for_day(day)
            keep = (secs >= s) & (secs < e)
            all_secs.append(secs[keep])
            all_mids.append(mids[keep])
            day += 86400
        return (
            np.concatenate(all_secs) if all_secs else np.array([], dtype=np.int64),
            np.concatenate(all_mids) if all_mids else np.array([]),
        )

    monkeypatch.setattr(zp._mpd, "_load_window_ticks", fake_load)
    monkeypatch.setattr(zp, "day_parquet_files", lambda *a, **k: [])
    monkeypatch.setattr(zp, "_ZP_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(zp, "NULL_HIST_DAYS", 20)
    monkeypatch.setattr(zp, "NULL_MIN_DAYS", 10)
    monkeypatch.setattr(zp, "M_REPS_DAY", 300)
    monkeypatch.setattr(zp, "M_REPS_LIVE", 200)
    zp._reset_caches()
    yield calls
    zp._reset_caches()


def _day(n: int) -> int:
    return _DAY0 + n * 86400


def test_completed_day_persists_and_disk_hit(zp_env):
    now = _day(40)  # day30 は完了日・履歴 20 日は十分
    roll1 = zp._zp_day_rollup("SYN", _day(30), now)
    assert roll1 is not None
    assert set(roll1) == {"kmin", "obs", "mean", "var"}
    assert roll1["obs"].sum() == zp.G_MINUTES  # 完了日は全分カウント
    path = zp._STORE.null_path("SYN", _day(30))
    assert path.is_file()
    # メモリ消去 → ディスクヒットで同値（tick 再読込は mgrid 不要のため増えない）
    zp._reset_caches()
    before = zp_env["load"]
    roll2 = zp._zp_day_rollup("SYN", _day(30), now)
    assert zp_env["load"] == before
    assert np.array_equal(roll1["obs"], roll2["obs"])
    assert np.array_equal(roll1["mean"], roll2["mean"])


def test_today_not_persisted_and_column_limited(zp_env):
    day = _day(30)
    now = day + (zp.SESSION_OPEN_MOD + 120) * 60  # セッション開始から 120 分経過の当日
    roll = zp._zp_day_rollup("SYN", day, now)
    assert roll is not None
    assert not zp._STORE.null_path("SYN", day).is_file()  # 当日は永続化しない
    assert roll["obs"].sum() <= 121  # 経過分までに限定（ffill 幻影滞在を数えない）
    assert roll["mean"].sum() <= 121 + 1e-9  # 帰無も同一カラム範囲


def test_insufficient_history_gives_none(zp_env, monkeypatch):
    monkeypatch.setattr(zp, "NULL_MIN_DAYS", 25)  # 履歴 20 日 < 25 → z 未定義
    now = _day(40)
    assert zp._zp_day_rollup("SYN", _day(21), now) is None
    # None も完了日として永続化され、再計算なしで None が返る
    assert zp._STORE.null_path("SYN", _day(21)).is_file()


def test_version_mismatch_forces_recompute(zp_env, monkeypatch):
    now = _day(40)
    zp._zp_day_rollup("SYN", _day(30), now)
    zp._reset_caches()
    monkeypatch.setattr(zp, "_ZP_CACHE_VERSION", 99)
    disk, _sig = zp._STORE.load_null(zp._STORE.null_path("SYN", _day(30)))
    assert disk is zp._CACHE_MISS  # 版数不一致は fail-safe で MISS


def test_compute_zp_profile_schema_and_sessions(zp_env):
    now = _day(40)
    t0, t1 = _day(28), _day(30)
    # 表示レンジは合成 mid の実域をカバーする広めの値
    prof = zp.compute_zp_profile(
        "SYN", t0, t1, 19800.0, 20200.0, 50, now=now,
        want_today=True, want_sessions=True,
    )
    for key in ("bins", "poc", "va_low", "va_high", "price_min", "price_max",
                "tpo_units", "n_bins", "z_max", "poc_star", "today", "sessions"):
        assert key in prof
    assert prof["poc"] == prof["poc_star"]
    assert prof["tpo_units"] > 0
    norms = [b["norm"] for b in prof["bins"]]
    assert all(0.0 <= v <= 1.0 for v in norms)
    assert len(prof["sessions"]) == 3  # 28/29/30 の 3 日
    for s in prof["sessions"]:
        assert set(s) == {"date", "tpo", "poc", "va_low", "va_high"}


def test_compute_zp_profile_deterministic(zp_env):
    now = _day(40)
    p1 = zp.compute_zp_profile("SYN", _day(29), _day(30), 19800.0, 20200.0, 40, now=now)
    zp._reset_caches()
    p2 = zp.compute_zp_profile("SYN", _day(29), _day(30), 19800.0, 20200.0, 40, now=now)
    assert p1 == p2  # seed 決定論＋キャッシュ経路差なし


def test_partial_rollup_columns(zp_env):
    now = _day(40)
    day = _day(30)
    lo = day + (zp.SESSION_OPEN_MOD + 100) * 60
    hi = day + (zp.SESSION_OPEN_MOD + 300) * 60
    roll = zp._zp_partial_rollup("SYN", lo, hi, now)
    assert roll is not None
    assert roll["obs"].sum() == 200  # カラム範囲 [100, 300) の 200 分ぶん


def test_window_aggregation_identity(zp_env):
    """窓 z = (Σobs − Σmean)/√(Σvar) の恒等式を、日別 rollup の手動合算と照合する。"""
    now = _day(40)
    days = [_day(29), _day(30)]
    rolls = [zp._zp_day_rollup("SYN", d, now) for d in days]
    assert all(r is not None for r in rolls)
    # fine セル（幅 10）と表示 bin が index 1:1 で整列するレンジ（size=40・ドリフト<0.5）
    pmin, pmax = 19800.0, 20199.0
    kw0 = int(np.floor(pmin / zp.GRID_W))
    size = int(np.floor(pmax / zp.GRID_W)) - kw0 + 1
    obs = np.zeros(size)
    mean = np.zeros(size)
    var = np.zeros(size)
    for r in rolls:
        off = r["kmin"] - kw0
        n = len(r["obs"])
        obs[off:off + n] += r["obs"]
        mean[off:off + n] += r["mean"]
        var[off:off + n] += r["var"]
    with np.errstate(invalid="ignore", divide="ignore"):
        z_manual = (obs - mean) / np.sqrt(var)
    z_manual[~np.isfinite(z_manual)] = 0.0
    prof = zp.compute_zp_profile("SYN", days[0], days[1], pmin, pmax, size, now=now)
    # n_bins = size ＝ 表示 bin と fine セルが 1:1 → bins[].tpo が z_manual と一致（丸め 2 桁）
    got = np.array([b["tpo"] for b in prof["bins"]])
    assert np.allclose(got, np.round(z_manual, 2), atol=0.011)
    assert prof["z_max"] == pytest.approx(round(float(z_manual.max()), 2), abs=0.011)


def test_day_source_signature_covers_two_utc_days(tmp_path):
    """ISSUE-078: セッション日は UTC 2 日を跨ぐため署名も両日 parquet を覆う。"""
    import pandas as pd
    from market_profile_api.compute.market_profile_zp_store import ZpStore
    calls = []

    def dpf(lo, hi, symbol=None):
        calls.append((lo, hi))
        return []

    store = ZpStore(
        grid_w=10.0,
        root_provider=lambda: str(tmp_path),
        default_root_provider=lambda: tmp_path,
        hist_days=250,
        m_reps=2000,
        cache_version_provider=lambda: 1,
        day_parquet_files=dpf,
    )
    store.day_source_signature("JP225", 1783890000)  # 2026-07-12 21:00 UTC（夏セッション始端）。
    assert calls and calls[0][0] == pd.Timestamp("2026-07-12")
    assert calls[0][1] == pd.Timestamp("2026-07-13")
