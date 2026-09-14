"""data_prep の単体テスト（手組み合成 M1・厳密値）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mp_stats import data_prep as dp


# --------------------------------------------------------------------------- #
# 合成 M1 ヘルパ
# --------------------------------------------------------------------------- #
def _bar(day_epoch: int, mod: int, o=100.0, h=None, lo=None, c=None):
    c = o if c is None else c
    h = max(o, c) if h is None else h
    lo = min(o, c) if lo is None else lo
    return {"epoch": day_epoch + mod * 60, "open": o, "high": h, "low": lo, "close": c}


def _m1(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    return df.sort_values("epoch").reset_index(drop=True)


_FRI = 1704412800   # 2024-01-05 (金) 00:00 UTC
_MON = 1704672000   # 2024-01-08 (月) 00:00 UTC
assert (_MON - _FRI) == 3 * 86400


# --------------------------------------------------------------------------- #
# セッション切出し / OC / CO / 週末 span
# --------------------------------------------------------------------------- #
def test_session_window_and_returns():
    rows = [
        _bar(_FRI, 60, o=999.0),                 # 01:00 → 窓外（除外）
        _bar(_FRI, 61, o=100.0, c=101.0),        # O_d = 100
        _bar(_FRI, 700, o=101.0, c=102.0),
        _bar(_FRI, 1438, o=102.0, c=110.0),      # C_d = 110
        _bar(_FRI, 1439, o=999.0),               # 23:59 → 窓外
        _bar(_MON, 61, o=112.0, c=112.0),        # 月曜
        _bar(_MON, 200, o=112.0, c=115.0),
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    assert sd.n_days == 2
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    assert f.o[0] == 100.0 and f.c[0] == 110.0
    assert f.r_oc[0] == pytest.approx(np.log(110.0 / 100.0))
    assert np.isnan(f.r_co[0])
    assert f.r_co[1] == pytest.approx(np.log(112.0 / 110.0))
    assert f.co_span_days[1] == 3.0  # 金→月


def test_min_bars_excludes_thin_days():
    rows = [_bar(_FRI, 61 + i, o=100.0) for i in range(10)]
    rows += [_bar(_MON, 61 + i, o=100.0) for i in range(2)]
    sd = dp.build_session_data(_m1(rows), min_bars=5)
    assert sd.n_days == 1
    assert sd.day_epoch[0] == _FRI


# --------------------------------------------------------------------------- #
# 5 分 RV（300 秒厳密ペアのみ）
# --------------------------------------------------------------------------- #
def test_rv_oc_strict_300s_pairs():
    rows = [
        _bar(_FRI, 65, c=100.0),   # 5 分境界
        _bar(_FRI, 70, c=101.0),   # +300s → 寄与
        _bar(_FRI, 75, c=102.0),   # +300s → 寄与
        _bar(_FRI, 85, c=103.0),   # +600s → 除外
        _bar(_FRI, 88, c=104.0),   # 非境界 → 除外
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    expected = np.log(101.0 / 100.0) ** 2 + np.log(102.0 / 101.0) ** 2
    assert f.rv_oc[0] == pytest.approx(expected, rel=1e-12)


# --------------------------------------------------------------------------- #
# ブラケット定義
# --------------------------------------------------------------------------- #
def test_bracket_minutes_single_source():
    bm = dp.bracket_minutes()
    assert bm.size == dp.K_BRACKETS == 46
    assert bm[0] == 29.0        # [01:01, 01:30)
    assert np.all(bm[1:-1] == 30.0)
    assert bm[-1] == 29.0       # [23:30, 23:58]
    assert bm.sum() == 1378.0


# --------------------------------------------------------------------------- #
# TPO（既知 high/low → N_d(p)/POC/conc 厳密値）
# --------------------------------------------------------------------------- #
def test_tpo_exact_counts_and_poc():
    # b0=[100,110], b1=[104,112], b2=[110,118]（重なりの梯子）
    rows = [
        _bar(_FRI, 61, o=100.0, h=110.0, lo=100.0, c=105.0),   # bracket 0
        _bar(_FRI, 95, o=105.0, h=112.0, lo=104.0, c=106.0),   # bracket 1
        _bar(_FRI, 121, o=106.0, h=118.0, lo=110.0, c=117.0),  # bracket 2
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    v = dp.VariantSpec("raw", 5)
    f = dp.build_daily_features(sd, variants=(v,), primary=v)
    # rows: [100,103.6],[103.6,107.2],[107.2,110.8],[110.8,114.4],[114.4,118]
    # overlap: b0→rows0-2, b1→rows1-3, b2→rows2-4 → N=[1,2,3,2,1]
    assert f.conc[v.key][0] == 3
    assert f.poc_price[v.key][0] == pytest.approx(109.0)
    # POC 行 (row2) を覆うのは b0,b1,b2 全部
    assert list(np.flatnonzero(f.poc_touch_primary[0])) == [0, 1, 2]
    assert f.poc_bracket_median[v.key][0] == 1.0
    assert f.poc_bracket_first[v.key][0] == 0.0


def test_tpo_ffill_counts_gap_dwell():
    # bar1(b0): 100 のみ。長い欠測（close=100 で滞在）→ bar2(b4): [115,120]
    rows = [
        _bar(_FRI, 61, o=100.0, h=100.0, lo=100.0, c=100.0),
        _bar(_FRI, 200, o=115.0, h=120.0, lo=115.0, c=120.0),
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    raw = dp.VariantSpec("raw", 4)
    ff = dp.VariantSpec("ffill", 4)
    f = dp.build_daily_features(sd, variants=(raw, ff), primary=raw)
    # day range [100,120] rows: [100,105],[105,110],[110,115],[115,120]
    # raw: b0=[100,100]→row0, b4=[115,120]→rows2,3 → N=[1,0,1,1] conc=1
    assert f.conc[raw.key][0] == 1
    # ffill: 欠測分は直前 close で滞在計上（bar2 の後もセッション末尾まで 120 に滞在）
    #   row0 = b0..b4 の 5 本（100 滞在）、row3 = b4 + b5..b45（120 滞在）= 42 本
    assert f.conc[ff.key][0] == 42
    assert f.poc_price[ff.key][0] == pytest.approx(117.5)


def test_grid_variant_fixed_row_width():
    rows = [
        _bar(_FRI, 61, o=100.0, h=130.0, lo=100.0, c=120.0),
        _bar(_FRI, 95, o=120.0, h=125.0, lo=105.0, c=110.0),
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    v = dp.VariantSpec("raw", 0, grid_w=10.0)
    f = dp.build_daily_features(sd, variants=(v,), primary=v)
    # span=30 → 3 行。両ブラケットが全域に近く重なる
    assert f.conc[v.key][0] == 2


def test_exclude_brackets_removes_contribution():
    rows = [
        _bar(_FRI, 61, o=100.0, h=110.0, lo=100.0, c=105.0),   # bracket 0
        _bar(_FRI, 95, o=105.0, h=112.0, lo=104.0, c=106.0),   # bracket 1
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    v = dp.VariantSpec("raw", 4)
    f_all = dp.build_daily_features(sd, variants=(v,), primary=v)
    f_ex = dp.build_daily_features(sd, variants=(v,), primary=v, exclude_brackets=(1,))
    assert f_all.conc[v.key][0] == 2
    assert f_ex.conc[v.key][0] == 1
    assert f_ex.excluded_brackets == (1,)


# --------------------------------------------------------------------------- #
# ブラケット統計（2-0 用）
# --------------------------------------------------------------------------- #
def test_bracket_freeze_stats():
    # bracket0: 同一 close 3 連（凍結様）、bracket1: 3 種の close
    rows = [
        _bar(_FRI, 61, c=100.0), _bar(_FRI, 62, c=100.0), _bar(_FRI, 63, c=100.0),
        _bar(_FRI, 90, c=101.0), _bar(_FRI, 91, c=102.0), _bar(_FRI, 92, c=101.5),
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    assert f.br_ndistinct[0, 0] == 1.0 and f.br_maxrun[0, 0] == 3.0
    assert f.br_ndistinct[0, 1] == 3.0 and f.br_maxrun[0, 1] == 1.0
    # bracket0 内 1 分 r² = 0（凍結）、bracket1 は正
    assert f.br_rv[0, 0] == 0.0
    assert f.br_rv[0, 1] > 0.0


# --------------------------------------------------------------------------- #
# 季節性 ŝ(b) / τ ブラケット
# --------------------------------------------------------------------------- #
def _features_with_br_ret(br_ret: np.ndarray) -> dp.DailyFeatures:
    D, K = br_ret.shape
    z = np.zeros(D)
    return dp.DailyFeatures(
        day=np.arange(D, dtype=np.int64) * 86400, o=z + 1, c=z + 1,
        day_high=z + 2, day_low=z + 1, r_oc=z, r_co=z, co_span_days=z + 1,
        rv_oc=z, n_bars=z + 100, bracket_minutes=dp.bracket_minutes(),
        br_ret=br_ret, br_rv=np.abs(br_ret), br_ndistinct=z[:, None] + 1,
        br_maxrun=z[:, None] + 1,
    )


def test_s_hat_full_recovers_pattern():
    rng = np.random.default_rng(21)
    D, K = 4000, dp.K_BRACKETS
    sigma = np.ones(K)
    sigma[10] = 2.0   # bracket10 は分散 4 倍
    sigma[20] = 0.5
    br_ret = rng.normal(size=(D, K)) * sigma
    s = dp.s_hat_full(_features_with_br_ret(br_ret))
    assert np.mean(s**2) == pytest.approx(1.0, rel=1e-9)     # 正規化
    assert s[10] / np.median(s) == pytest.approx(2.0, rel=0.1)
    assert s[20] / np.median(s) == pytest.approx(0.5, rel=0.1)


def test_s_hat_expanding_no_lookahead_and_warmup():
    rng = np.random.default_rng(22)
    D, K = 300, dp.K_BRACKETS
    br_ret = rng.normal(size=(D, K))
    br_ret[250:, 5] *= 10.0  # 後半だけ bracket5 の分散が跳ねる
    f = _features_with_br_ret(br_ret)
    s = dp.s_hat_expanding(f, warmup=250)
    assert np.all(np.isnan(s[:250]))
    # day250 の ŝ は day<250 のみ由来 → bracket5 は跳ね前 ≈ 1
    assert s[250, 5] == pytest.approx(1.0, abs=0.15)


def test_tau_assignment_uniform_matches_calendar_scale():
    assign = dp.tau_bracket_of_mod(np.ones(dp.K_BRACKETS))
    mods = np.arange(dp.SESSION_OPEN_MOD, dp.SESSION_CLOSE_MOD + 1)
    counts = np.bincount(assign[mods], minlength=dp.K_BRACKETS)
    assert assign[dp.SESSION_OPEN_MOD - 1] == -1  # 窓外
    assert np.all(counts >= 29) and np.all(counts <= 31)  # ほぼ等分
    assert np.all(np.diff(assign[mods]) >= 0)  # 時間単調


def test_tau_assignment_concentrated_variance():
    s2 = np.zeros(dp.K_BRACKETS)
    s2[0] = 1.0  # 全分散が暦ブラケット 0 に集中
    assign = dp.tau_bracket_of_mod(s2)
    mods = np.arange(dp.SESSION_OPEN_MOD, dp.SESSION_CLOSE_MOD + 1)
    b0 = mods[dp.calendar_bracket_of_mod(mods) == 0]
    rest = mods[dp.calendar_bracket_of_mod(mods) > 0]
    # 高ボラ帯（29 分しかない）が全 46 バケットへ引き伸ばされる → 29 分がほぼ全て別バケット
    assert np.unique(assign[b0]).size >= 25
    assert np.all(assign[rest] == dp.K_BRACKETS - 1)  # ゼロボラ帯は末尾に潰れる


def test_tpo_tau_series_uniform_equals_calendarish():
    rows = [
        _bar(_FRI, 61, o=100.0, h=110.0, lo=100.0, c=105.0),
        _bar(_FRI, 95, o=105.0, h=112.0, lo=104.0, c=106.0),
        _bar(_FRI, 121, o=106.0, h=118.0, lo=110.0, c=117.0),
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    v = dp.VariantSpec("raw", 5)
    f = dp.build_daily_features(sd, variants=(v,), primary=v)
    out = dp.tpo_tau_series(sd, f, v, np.ones(dp.K_BRACKETS))
    # 一様 s² → τ 分割は暦 30 分割とほぼ同じ → 同じ梯子 → conc=3, poc=109
    assert out["conc"][0] == 3
    assert out["poc_price"][0] == pytest.approx(109.0)


def test_tpo_tau_series_expanding_nan_rows():
    rows = [
        _bar(_FRI, 61, o=100.0, h=110.0, lo=100.0, c=105.0),
        _bar(_FRI, 95, o=105.0, h=112.0, lo=104.0, c=106.0),
        _bar(_MON, 61, o=112.0, h=118.0, lo=110.0, c=117.0),
        _bar(_MON, 95, o=117.0, h=120.0, lo=115.0, c=118.0),
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    v = dp.VariantSpec("raw", 4)
    f = dp.build_daily_features(sd, variants=(v,), primary=v)
    s2 = np.full((2, dp.K_BRACKETS), np.nan)
    s2[1] = 1.0  # day0 は未確定
    out = dp.tpo_tau_series(sd, f, v, s2)
    assert np.isnan(out["conc"][0])
    assert not np.isnan(out["conc"][1])


# --------------------------------------------------------------------------- #
# ルックアヘッド assert
# --------------------------------------------------------------------------- #
def test_assert_no_lookahead_daily():
    day = np.array([0, 86400], dtype=np.int64)
    dp.assert_no_lookahead_daily(day, day + 86400)
    with pytest.raises(AssertionError):
        dp.assert_no_lookahead_daily(day, day)
