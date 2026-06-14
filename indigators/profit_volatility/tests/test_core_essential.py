"""本質コア（OHLC4 対数変化・標準化 1 系列）の検証。

49 系列合算の本質（実効 1 次元 = 加重値 OHLC4 の 6 本変化）だけを残し、乖離を値幅から
対数差 ``ln(ohlc4[a]/ohlc4[a-period])`` に変えて価格水準依存を除去したパターンを固定する。
核となる固定点:
    * 対数変化式と warm-up→NaN（算出不能区間）。
    * **スケール不変性**（全価格を定数倍しても乖離が不変＝価格水準非依存）。
    * 標準化（有効点で mean≈0 / std≈1）と ±3.29σ クランプ。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    CoreVolatilityResult,
    compute_core_divergence,
    compute_core_volatility,
)


# --------------------------------------------------------------------------- helpers
def _sample_ohlc(n: int = 60, seed: int = 0, base: float = 100.0):
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.normal(0, 1.0, n))
    high = close + rng.uniform(0.2, 1.0, n)
    low = close - rng.uniform(0.2, 1.0, n)
    open_ = close + rng.normal(0, 0.3, n)
    return open_, high, low, close


# ============================================================ compute_core_divergence
def test_core_divergence_is_log_ratio_of_ohlc4():
    o, h, low, c = _sample_ohlc(n=20, seed=1)
    period = 6
    d = compute_core_divergence(o, h, low, c, period=period)
    w = (o + h + low + c) / 4.0
    expected = np.full(20, np.nan)
    expected[period:] = np.log(w[period:] / w[:-period])
    np.testing.assert_allclose(d[period:], expected[period:], rtol=1e-12, atol=1e-12)


def test_core_divergence_warmup_is_nan():
    o, h, low, c = _sample_ohlc(n=15, seed=2)
    d = compute_core_divergence(o, h, low, c, period=6)
    assert np.all(np.isnan(d[:6]))  # warm-up（a<period）は算出不能 → NaN
    assert np.all(np.isfinite(d[6:]))


def test_core_divergence_is_scale_invariant():
    # 価格水準依存の除去の核: 全価格を定数倍しても対数変化は不変。
    o, h, low, c = _sample_ohlc(n=40, seed=3, base=100.0)
    d1 = compute_core_divergence(o, h, low, c, period=6)
    k = 7.3  # 任意の価格スケール
    d2 = compute_core_divergence(o * k, h * k, low * k, c * k, period=6)
    np.testing.assert_allclose(d1[6:], d2[6:], rtol=1e-12, atol=1e-12)


def test_core_divergence_period_below_2_raises():
    o, h, low, c = _sample_ohlc(n=10)
    with pytest.raises(ValueError):
        compute_core_divergence(o, h, low, c, period=1)


# ============================================================= compute_core_volatility
def test_core_volatility_returns_result_with_fields():
    o, h, low, c = _sample_ohlc(n=50, seed=4)
    res = compute_core_volatility(o, h, low, c, period=6)
    assert isinstance(res, CoreVolatilityResult)
    assert res.raw_level_count.shape == (50,)
    assert res.level_count_clamped.shape == (50,)
    assert res.divergence.shape == (50,)
    assert "up_329" in res.levels and "dn_329" in res.levels


def test_core_volatility_warmup_is_nan_in_output():
    o, h, low, c = _sample_ohlc(n=50, seed=5)
    res = compute_core_volatility(o, h, low, c, period=6)
    assert np.all(np.isnan(res.raw_level_count[:6]))
    assert np.all(np.isnan(res.level_count_clamped[:6]))
    assert np.all(np.isfinite(res.raw_level_count[6:]))


def test_core_volatility_standardized_mean0_std1_over_valid():
    o, h, low, c = _sample_ohlc(n=200, seed=6)
    res = compute_core_volatility(o, h, low, c, period=6)
    z = res.raw_level_count[6:]  # 有効点
    assert abs(float(np.mean(z))) < 1e-9
    assert abs(float(np.std(z)) - 1.0) < 1e-9


def test_core_volatility_is_scale_invariant():
    # 標準化系列も価格水準に依存しない（対数差ゆえ）。
    o, h, low, c = _sample_ohlc(n=120, seed=7, base=100.0)
    a = compute_core_volatility(o, h, low, c, period=6).raw_level_count
    b = compute_core_volatility(o * 12.5, h * 12.5, low * 12.5, c * 12.5, period=6).raw_level_count
    np.testing.assert_allclose(a[6:], b[6:], rtol=1e-10, atol=1e-10)


def test_core_volatility_clamps_to_sigma_329_band():
    o, h, low, c = _sample_ohlc(n=120, seed=8)
    # 1 点だけ強い乖離を作りクランプを発火させる。
    c[60] += 30.0
    h[60] += 30.0
    res = compute_core_volatility(o, h, low, c, period=6)
    upper = res.levels["up_329"]
    lower = res.levels["dn_329"]
    valid = ~np.isnan(res.level_count_clamped)
    assert np.all(res.level_count_clamped[valid] <= upper + 1e-12)
    assert np.all(res.level_count_clamped[valid] >= lower - 1e-12)
    raw = res.raw_level_count[~np.isnan(res.raw_level_count)]
    assert raw.max() > upper or raw.min() < lower


def test_core_volatility_arrays_are_readonly():
    o, h, low, c = _sample_ohlc(n=30, seed=9)
    res = compute_core_volatility(o, h, low, c, period=6)
    assert res.raw_level_count.flags.writeable is False
    assert res.level_count_clamped.flags.writeable is False
    assert res.divergence.flags.writeable is False
    with pytest.raises(ValueError):
        res.raw_level_count[10] = 0.0


def test_core_volatility_length_mismatch_raises():
    o = np.array([100.0, 101.0, 102.0])
    h = np.array([101.0, 102.0])
    low = np.array([99.0, 100.0, 101.0])
    c = np.array([100.5, 101.5, 102.5])
    with pytest.raises(ValueError):
        compute_core_volatility(o, h, low, c)


def test_core_volatility_period_below_2_raises():
    o, h, low, c = _sample_ohlc(n=20, seed=10)
    with pytest.raises(ValueError):
        compute_core_volatility(o, h, low, c, period=1)


# ======================================================= 因果ローリング窓（look-ahead 除去）
def test_causal_warmup_covers_period_plus_window():
    # 因果版 warm-up は period+window-1 付近まで NaN（窓を満たす最初のバーから有効）。
    o, h, low, c = _sample_ohlc(n=200, seed=11)
    period, window = 6, 60
    res = compute_core_volatility(o, h, low, c, period=period, window=window)
    z = res.raw_level_count
    first_valid = period + window - 1
    assert np.all(np.isnan(z[:first_valid]))
    assert np.isfinite(z[first_valid])


def test_causal_value_does_not_repaint_when_future_added():
    # 因果版の核心: 確定した過去バーの値は、後ろに未来を足しても変わらない。
    o, h, low, c = _sample_ohlc(n=400, seed=12)
    period, window = 6, 60
    bar = 250  # 窓を満たした確定済み過去バー
    v_short = compute_core_volatility(
        o[:300], h[:300], low[:300], c[:300], period=period, window=window
    ).raw_level_count[bar]
    v_long = compute_core_volatility(
        o, h, low, c, period=period, window=window
    ).raw_level_count[bar]
    assert np.isfinite(v_short)
    np.testing.assert_allclose(v_short, v_long, rtol=1e-12, atol=1e-12)


def test_fullsample_value_does_repaint_when_future_added():
    # 対照: 全期間版（window=None）は未来追加で過去の値が変わる（repaint する）。
    o, h, low, c = _sample_ohlc(n=400, seed=12)
    bar = 250
    v_short = compute_core_volatility(
        o[:300], h[:300], low[:300], c[:300], period=6, window=None
    ).raw_level_count[bar]
    v_long = compute_core_volatility(
        o, h, low, c, period=6, window=None
    ).raw_level_count[bar]
    assert not np.isclose(v_short, v_long)


def test_causal_uses_only_trailing_window():
    # 因果版バー a の値は、a より前のデータのみに依存（a 以降を改変しても不変）。
    o, h, low, c = _sample_ohlc(n=300, seed=13)
    period, window = 6, 60
    bar = 200
    base = compute_core_volatility(o, h, low, c, period=period, window=window)
    # バー bar 以降の close を改変
    c2 = c.copy()
    c2[bar + 1:] += 50.0
    mod = compute_core_volatility(o, h, low, c2, period=period, window=window)
    np.testing.assert_allclose(
        base.raw_level_count[bar], mod.raw_level_count[bar], rtol=1e-12, atol=1e-12
    )


def test_causal_is_scale_invariant():
    o, h, low, c = _sample_ohlc(n=250, seed=14, base=100.0)
    a = compute_core_volatility(o, h, low, c, period=6, window=60).raw_level_count
    b = compute_core_volatility(o * 9.0, h * 9.0, low * 9.0, c * 9.0,
                                period=6, window=60).raw_level_count
    np.testing.assert_allclose(a[100:], b[100:], rtol=1e-10, atol=1e-10, equal_nan=True)
