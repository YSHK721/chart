"""PRO!fit_Volatility コア計算の検証（元 MQL4 / PS.mqh iVOLATILITY を 1:1 固定）。

手計算可能な小入力で iVOLATILITY（49 系列 = 7×7 の X[a]-Y[a-period]）・warm-up→0・
49 系列集計・σ12 水準・クランプ・DTO 不変性・例外を固定する。

iVOLATILITY の applied_price は 2 桁コード ``XY``（X=1 桁目=price_A=現足側 x_digit,
Y=2 桁目=price_B=period 本前側 y_digit、各 0..6）。digit→系列は MQL4 流の 0 始まり:
0=Close,1=Open,2=High,3=Low,4=Median,5=Typical,6=Weighted。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# src（profit_volatility 配下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    SIGMA_LEVELS,
    VOLATILITY_MODES,
    VolatilityResult,
    compute_level_count,
    compute_sigma_levels,
    compute_volatility,
    compute_volatility_full,
    compute_volatility_levels,
    ps_level_count,
)


# --------------------------------------------------------------------------- helpers
def _price_of(digit: int, o, h, low, c):
    """iVOLATILITY の素の MT4 価格式（digit 0 始まり）。
    median=(H+L)/2, typical=(H+L+C)/3, weighted=(H+L+C+O)/4。
    common.applied_price の weighted=(H+L+2C)/4 とは異なる。"""
    o = np.asarray(o, float); h = np.asarray(h, float)
    low = np.asarray(low, float); c = np.asarray(c, float)
    if digit == 0:
        return c
    if digit == 1:
        return o
    if digit == 2:
        return h
    if digit == 3:
        return low
    if digit == 4:
        return (h + low) / 2.0
    if digit == 5:
        return (h + low + c) / 3.0
    if digit == 6:
        return (h + low + c + o) / 4.0
    raise AssertionError(digit)


# ==================================================== compute_volatility（iVOLATILITY）
def test_compute_volatility_mode00_close_minus_close():
    # X=0(Close),Y=0(Close): res[a] = C[a] - C[a-period]、warm-up a<period は pY=0→res=C[a]。
    o = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    h = o + 1.0
    low = o - 1.0
    c = np.array([10.0, 12.0, 11.0, 15.0, 14.0, 18.0])
    out = compute_volatility(o, h, low, c, period=2, x_digit=0, y_digit=0)
    expected = np.zeros(6)
    for a in range(2, 6):
        expected[a] = c[a] - c[a - 2]
    np.testing.assert_allclose(out, expected, rtol=1e-12, atol=1e-12)
    # warm-up（a<2）は元ループ未計算で res=0（res=C[a] ではない点を discriminating 固定）。
    assert out[0] == 0.0 and out[1] == 0.0


def test_compute_volatility_mode06_close_minus_weighted():
    # X=0(Close),Y=6(Weighted): res[a] = C[a] - W[a-period]、W=(H+L+C+O)/4、period=2。
    o = np.array([5.0, 6.0, 7.0, 8.0]); h = np.array([10.0, 12.0, 14.0, 16.0])
    low = np.array([2.0, 3.0, 4.0, 5.0]); c = np.array([8.0, 9.0, 10.0, 11.0])
    out = compute_volatility(o, h, low, c, period=2, x_digit=0, y_digit=6)
    wa = (h + low + c + o) / 4.0  # raw MT4 weighted
    expected = np.zeros(4)
    for a in range(2, 4):
        expected[a] = c[a] - wa[a - 2]
    np.testing.assert_allclose(out, expected, rtol=1e-12, atol=1e-12)
    # warm-up（a<2）は元ループ未計算で res=0。
    assert out[0] == 0.0 and out[1] == 0.0
    # common の weighted=(H+L+2C)/4 とは一致しないことを明示。
    wrong = (h + low + 2.0 * c) / 4.0
    assert not np.allclose(wa, wrong)


def test_compute_volatility_mode60_weighted_minus_close():
    # X=6(Weighted),Y=0(Close): res[a] = W[a] - C[a-period]、period=2。
    o = np.array([5.0, 6.0, 7.0, 8.0, 9.0]); h = np.array([10.0, 12.0, 14.0, 16.0, 18.0])
    low = np.array([2.0, 3.0, 4.0, 5.0, 6.0]); c = np.array([8.0, 9.0, 10.0, 11.0, 12.0])
    out = compute_volatility(o, h, low, c, period=2, x_digit=6, y_digit=0)
    wa = (h + low + c + o) / 4.0
    expected = np.zeros(5)
    for a in range(2, 5):
        expected[a] = wa[a] - c[a - 2]
    np.testing.assert_allclose(out, expected, rtol=1e-12, atol=1e-12)
    # warm-up（a<2）は元ループ未計算で res=0。
    assert out[0] == 0.0 and out[1] == 0.0


def test_compute_volatility_high_low_cross_pair():
    # X=2(High),Y=3(Low): res[a] = H[a] - L[a-period]、period=2。
    o = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    h = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    low = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    c = np.array([5.0, 6.0, 7.0, 8.0, 9.0])
    out = compute_volatility(o, h, low, c, period=2, x_digit=2, y_digit=3)
    expected = np.zeros(5)
    for a in range(2, 5):
        expected[a] = h[a] - low[a - 2]
    np.testing.assert_allclose(out, expected, rtol=1e-12, atol=1e-12)


def test_compute_volatility_median_typical_formulas():
    # X=4(Median),Y=5(Typical): median=(H+L)/2, typical=(H+L+C)/3、period=2。
    o = np.array([1.0, 2.0, 3.0, 4.0, 5.0]); h = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    low = np.array([2.0, 4.0, 6.0, 8.0, 10.0]); c = np.array([5.0, 7.0, 9.0, 11.0, 13.0])
    out_m = compute_volatility(o, h, low, c, period=2, x_digit=4, y_digit=5)
    med = (h + low) / 2.0
    typ = (h + low + c) / 3.0
    expected = np.zeros(5)
    for a in range(2, 5):
        expected[a] = med[a] - typ[a - 2]
    np.testing.assert_allclose(out_m, expected, rtol=1e-12, atol=1e-12)


def test_compute_volatility_warmup_is_zero():
    # warm-up a<period: 元 OnCalculate の for i<limit-inpPeriod が未計算 → res=0。
    # （res=X[a] ではなく res=0 となる点を discriminating 固定）。
    o = np.arange(1.0, 9.0); h = o + 1; low = o - 1; c = o + 0.5
    out = compute_volatility(o, h, low, c, period=3, x_digit=0, y_digit=1)
    # warm-up（a<3）は未計算で res=0。
    for a in range(3):
        assert out[a] == 0.0
    # a>=period は C[a]-O[a-period]。
    assert out[3] == c[3] - o[0]


def test_compute_volatility_period_below_2_raises():
    # 元 inpPeriod=6。period<2 は ValueError。
    o = np.array([1.0, 2.0, 3.0]); h = o + 1; low = o - 1; c = o
    with pytest.raises(ValueError):
        compute_volatility(o, h, low, c, period=1, x_digit=0, y_digit=0)
    with pytest.raises(ValueError):
        compute_volatility(o, h, low, c, period=0, x_digit=0, y_digit=0)


def test_compute_volatility_unknown_digit_raises():
    o = np.array([1.0, 2.0, 3.0]); h = o + 1; low = o - 1; c = o
    with pytest.raises(ValueError):
        compute_volatility(o, h, low, c, period=2, x_digit=7, y_digit=0)
    with pytest.raises(ValueError):
        compute_volatility(o, h, low, c, period=2, x_digit=0, y_digit=7)


# =========================================== σ12 水準（compute_volatility_levels）
def test_compute_sigma_levels_alias_equals_compute_sigma_levels():
    lc = np.array([0.5, -0.3, 1.2, -0.8, 0.1, 2.0])
    assert compute_volatility_levels(lc) == compute_sigma_levels(lc)


def test_sigma_levels_has_12_keys():
    lc = np.array([0.5, -0.3, 1.2, -0.8, 0.1, 2.0, -1.5, 0.9])
    levels = compute_volatility_levels(lc)
    assert len(levels) == 12
    for key in ("up_067", "up_128", "up_165", "up_196", "up_258", "up_329",
                "dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329"):
        assert key in levels


# =============================================== 49 系列集計（compute_level_count）
def test_volatility_modes_order_and_init():
    # 49 modes 順序: 00,01..06, 10..16, ..., 60..66（X=0..6, 各 Y=0..6）。
    assert len(VOLATILITY_MODES) == 49
    expected = [(x, y) for x in range(7) for y in range(7)]
    assert list(VOLATILITY_MODES) == expected
    # 最初の mode が (0,0)=00（initialization 対象）。
    assert VOLATILITY_MODES[0] == (0, 0)
    assert VOLATILITY_MODES[-1] == (6, 6)


def test_compute_level_count_49_series_first_init_rest_accumulate():
    # 元 OnCalculate: mode 00 が初期化、残り 48 系列が加算。
    rng = np.random.default_rng(42)
    n = 24
    o = rng.uniform(100, 110, n)
    h = o + rng.uniform(0.5, 2.0, n)
    low = o - rng.uniform(0.5, 2.0, n)
    c = low + rng.uniform(0.0, (h - low))

    out = compute_level_count(o, h, low, c, period=6)

    # 参照: VOLATILITY_MODES 順（00..66）で 49 系列を集計、mode 00 のみ initialization。
    ref = None
    for k, (xd, yd) in enumerate(VOLATILITY_MODES):
        vol = compute_volatility(o, h, low, c, period=6, x_digit=xd, y_digit=yd)
        ref = ps_level_count(vol, ref, initialization=(k == 0))
    np.testing.assert_allclose(out, ref, rtol=0, atol=0)


def test_compute_level_count_has_49_series():
    # 集計は 49 系列であること。
    assert len(VOLATILITY_MODES) == 49


# =================================================== compute_volatility_full / DTO
def test_compute_volatility_full_returns_result_with_fields():
    rng = np.random.default_rng(1)
    n = 40
    o = rng.uniform(100, 110, n)
    h = o + rng.uniform(0.5, 2.0, n)
    low = o - rng.uniform(0.5, 2.0, n)
    c = low + rng.uniform(0.0, (h - low))
    res = compute_volatility_full(o, h, low, c)
    assert isinstance(res, VolatilityResult)
    assert res.raw_level_count.shape == (n,)
    assert res.level_count_clamped.shape == (n,)
    assert "up_329" in res.levels and "dn_329" in res.levels


def test_compute_volatility_full_clamps_to_sigma_329_band():
    rng = np.random.default_rng(0)
    n = 80
    base = 100.0
    o = np.full(n, base) + rng.normal(0, 0.05, n)
    h = o + 0.1
    low = o - 0.1
    c = o + rng.normal(0, 0.05, n)
    c[40] += 5.0; h[40] += 5.0
    c[41] += 5.2; h[41] += 5.2
    res = compute_volatility_full(o, h, low, c)
    upper = res.levels["up_329"]
    lower = res.levels["dn_329"]
    assert np.all(res.level_count_clamped <= upper + 1e-12)
    assert np.all(res.level_count_clamped >= lower - 1e-12)
    assert res.raw_level_count.max() > upper or res.raw_level_count.min() < lower
    expected = np.clip(res.raw_level_count, lower, upper)
    np.testing.assert_allclose(res.level_count_clamped, expected, rtol=0, atol=0)


def test_compute_volatility_full_arrays_are_readonly():
    rng = np.random.default_rng(5)
    n = 20
    o = rng.uniform(100, 110, n)
    h = o + 1.0; low = o - 1.0; c = o + 0.2
    res = compute_volatility_full(o, h, low, c)
    assert res.raw_level_count.flags.writeable is False
    assert res.level_count_clamped.flags.writeable is False
    with pytest.raises(ValueError):
        res.raw_level_count[0] = 0.0


def test_compute_volatility_full_is_frozen_dto():
    rng = np.random.default_rng(9)
    n = 15
    o = rng.uniform(100, 110, n)
    h = o + 1.0; low = o - 1.0; c = o + 0.1
    res = compute_volatility_full(o, h, low, c)
    with pytest.raises(Exception):
        res.raw_level_count = np.zeros(n)  # type: ignore[misc]


def test_compute_volatility_full_ohlc_length_mismatch_raises():
    o = np.array([1.0, 2.0, 3.0])
    h = np.array([2.0, 3.0])
    low = np.array([0.5, 1.0, 1.5])
    c = np.array([1.5, 2.5, 2.0])
    with pytest.raises(ValueError):
        compute_volatility_full(o, h, low, c)


def test_compute_volatility_full_period_below_2_raises():
    o = np.arange(1.0, 11.0); h = o + 1; low = o - 1; c = o + 0.5
    with pytest.raises(ValueError):
        compute_volatility_full(o, h, low, c, period=1)


def test_sigma_levels_constant():
    assert SIGMA_LEVELS == (0.67, 1.28, 1.65, 1.96, 2.58, 3.29)
