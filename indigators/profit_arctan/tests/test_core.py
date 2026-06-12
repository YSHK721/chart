"""PRO!fit_Arctan コア計算の検証（元 MQL4 / PS.mqh iARCTAN を 1:1 固定）。

手計算可能な小入力で iARCTAN（MA 隣接差の atan・度変換）・B 未確定→0・7 価格集計・
σ12 水準・クランプ・DTO 不変性・例外を固定する。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

# src（profit_arctan 配下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    APPLIED_PRICES,
    SIGMA_LEVELS,
    ArctanResult,
    compute_arctan,
    compute_arctan_full,
    compute_arctan_levels,
    compute_level_count,
    compute_sigma_levels,
    ps_level_count,
)

from common import AppliedPrice, applied_price  # noqa: E402

_PI = 3.14159265359
_RAD2DEG = 180.0 / _PI


# --------------------------------------------------------------------------- helpers
def _ema_buffer(price: np.ndarray, period: int) -> np.ndarray:
    """EMA on_buffer 相当（begin=0, smooth=2/(1+period)）の手計算参照。"""
    sf = 2.0 / (1.0 + period)
    n = price.size
    buf = np.zeros(n)
    buf[0] = price[0]
    for i in range(1, period):
        buf[i] = price[i] * sf + buf[i - 1] * (1.0 - sf)
    for i in range(period, n):
        buf[i] = price[i] * sf + buf[i - 1] * (1.0 - sf)
    return buf


def _sma_buffer(price: np.ndarray, period: int) -> np.ndarray:
    """SMA on_buffer 相当（begin=0, warm-up は 0）の手計算参照。"""
    n = price.size
    buf = np.zeros(n)
    buf[period - 1] = float(np.sum(price[:period])) / period
    for i in range(period, n):
        buf[i] = buf[i - 1] + (price[i] - price[i - period]) / period
    return buf


# =========================================================== compute_arctan（iARCTAN）
def test_compute_arctan_ema_matches_manual_atan_degree():
    # EMA(period=2) の隣接差を atan→度に変換した手計算値と 1:1 一致。
    price = np.array([10.0, 12.0, 11.0, 15.0, 14.0])
    ma = _ema_buffer(price, 2)
    expected = np.zeros(5)
    for i in range(1, 5):  # i==0 は B 未確定→0
        expected[i] = math.atan(ma[i] - ma[i - 1]) / 0.1 * _RAD2DEG
    out = compute_arctan(price, period=2, ma_method=1, bar_width=0.1)
    np.testing.assert_allclose(out, expected, rtol=1e-12, atol=1e-12)
    # 具体値（手計算）: arctan[1] = atan(11.3333-10)/0.1*180/pi ≈ 531.3010235
    assert out[1] == pytest.approx(531.3010235415248, rel=1e-9)


def test_compute_arctan_first_bar_is_zero_when_B_undefined():
    # i==0 は前足なし（元 B==NULL）→ 0。
    price = np.array([10.0, 12.0, 11.0, 15.0])
    out = compute_arctan(price, period=2, ma_method=1, bar_width=0.1)
    assert out[0] == 0.0


def test_compute_arctan_zero_when_prev_ma_is_zero_warmup():
    # SMA(period=3) の warm-up（ma[i-1]==0）では arctan[i]=0。i=0,1,2 が 0。
    price = np.array([10.0, 12.0, 11.0, 15.0, 14.0, 13.0])
    ma = _sma_buffer(price, 3)
    out = compute_arctan(price, period=3, ma_method=0, bar_width=0.1)
    assert out[0] == 0.0
    assert out[1] == 0.0  # ma[0]==0（warm-up）→ B 未確定
    assert out[2] == 0.0  # ma[1]==0（warm-up）→ B 未確定
    expected3 = math.atan(ma[3] - ma[2]) / 0.1 * _RAD2DEG
    assert out[3] == pytest.approx(expected3, rel=1e-12)
    assert out[3] != 0.0


def test_compute_arctan_sma_method0_matches_manual():
    price = np.array([10.0, 12.0, 11.0, 15.0, 14.0, 13.0])
    ma = _sma_buffer(price, 3)
    expected = np.zeros(price.size)
    for i in range(price.size):
        if i == 0 or ma[i - 1] == 0.0:
            expected[i] = 0.0
        else:
            expected[i] = math.atan(ma[i] - ma[i - 1]) / 0.1 * _RAD2DEG
    out = compute_arctan(price, period=3, ma_method=0, bar_width=0.1)
    np.testing.assert_allclose(out, expected, rtol=1e-12, atol=1e-12)


def test_compute_arctan_all_four_ma_methods_run():
    # ma_method 0..3（SMA/EMA/SMMA/LWMA）すべて実行でき、非ゼロ値を持つ。
    price = np.array([10.0, 12.0, 11.0, 15.0, 14.0, 13.0, 16.0, 12.0])
    for method in (0, 1, 2, 3):
        out = compute_arctan(price, period=3, ma_method=method, bar_width=0.1)
        assert out.shape == price.shape
        assert np.any(out != 0.0)


def test_compute_arctan_bar_width_scales_inverse():
    # bar_width はそのまま除算（小さいほど大きい角度）。
    price = np.array([10.0, 12.0, 11.0, 15.0, 14.0])
    a = compute_arctan(price, period=2, ma_method=1, bar_width=0.1)
    b = compute_arctan(price, period=2, ma_method=1, bar_width=0.2)
    np.testing.assert_allclose(a[1:], b[1:] * 2.0, rtol=1e-12)


def test_compute_arctan_period_below_2_raises():
    price = np.array([10.0, 12.0, 11.0])
    with pytest.raises(ValueError):
        compute_arctan(price, period=1, ma_method=1, bar_width=0.1)


def test_compute_arctan_unknown_ma_method_raises():
    price = np.array([10.0, 12.0, 11.0, 15.0])
    with pytest.raises(ValueError):
        compute_arctan(price, period=2, ma_method=99, bar_width=0.1)


# =========================================== σ12 水準（compute_arctan_levels）
def test_compute_sigma_levels_alias_equals_compute_arctan_levels():
    # compute_arctan_levels は compute_sigma_levels と同値。
    lc = np.array([0.5, -0.3, 1.2, -0.8, 0.1, 2.0])
    assert compute_arctan_levels(lc) == compute_sigma_levels(lc)


def test_sigma_levels_has_12_keys():
    lc = np.array([0.5, -0.3, 1.2, -0.8, 0.1, 2.0, -1.5, 0.9])
    levels = compute_arctan_levels(lc)
    assert len(levels) == 12  # up_* 6本 + dn_* 6本
    for key in ("up_067", "up_128", "up_165", "up_196", "up_258", "up_329",
                "dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329"):
        assert key in levels


# =================================================== 7 価格集計（compute_level_count）
def test_compute_level_count_seven_prices_w_init_rest_accumulate():
    # W が初期化・T..C が加算される順序を、参照集計と 1:1 一致で固定。
    rng = np.random.default_rng(42)
    n = 20
    o = rng.uniform(100, 110, n)
    h = o + rng.uniform(0.5, 2.0, n)
    low = o - rng.uniform(0.5, 2.0, n)
    c = low + rng.uniform(0.0, (h - low))

    out = compute_level_count(o, h, low, c, period=6, ma_method=1, bar_width=0.1)

    # 参照: W=初期化, T,M,H,L,O,C=加算（元の7回呼び出し順）
    order = [
        AppliedPrice.WEIGHTED, AppliedPrice.TYPICAL, AppliedPrice.MEDIAN,
        AppliedPrice.HIGH, AppliedPrice.LOW, AppliedPrice.OPEN, AppliedPrice.CLOSE,
    ]
    ref = None
    for k, kind in enumerate(order):
        price = applied_price(kind, o, h, low, c)
        arc = compute_arctan(price, period=6, ma_method=1, bar_width=0.1)
        ref = ps_level_count(arc, ref, initialization=(k == 0))
    np.testing.assert_allclose(out, ref, rtol=0, atol=0)


def test_compute_level_count_prices_are_distinct_series():
    # 各適用価格が異なる系列であること（applied_price 別の arctan が異なる）。
    rng = np.random.default_rng(7)
    n = 20
    o = rng.uniform(100, 110, n)
    h = o + rng.uniform(0.5, 2.0, n)
    low = o - rng.uniform(0.5, 2.0, n)
    c = low + rng.uniform(0.0, (h - low))
    arc_w = compute_arctan(
        applied_price(AppliedPrice.WEIGHTED, o, h, low, c), period=6, ma_method=1, bar_width=0.1
    )
    arc_h = compute_arctan(
        applied_price(AppliedPrice.HIGH, o, h, low, c), period=6, ma_method=1, bar_width=0.1
    )
    arc_l = compute_arctan(
        applied_price(AppliedPrice.LOW, o, h, low, c), period=6, ma_method=1, bar_width=0.1
    )
    assert not np.allclose(arc_w, arc_h)
    assert not np.allclose(arc_h, arc_l)


def test_applied_price_weighted_uses_common_mapping():
    # PS.mqh の 0-6 表でなく common.AppliedPrice（WEIGHTED=(h+l+2c)/4）を使う。
    o = np.array([5.0, 6.0]); h = np.array([10.0, 12.0])
    low = np.array([2.0, 3.0]); c = np.array([8.0, 9.0])
    w = applied_price(AppliedPrice.WEIGHTED, o, h, low, c)
    np.testing.assert_allclose(w, (h + low + 2.0 * c) / 4.0)


# =========================================================== compute_arctan_full / DTO
def test_compute_arctan_full_returns_arctan_result_with_fields():
    rng = np.random.default_rng(1)
    n = 30
    o = rng.uniform(100, 110, n)
    h = o + rng.uniform(0.5, 2.0, n)
    low = o - rng.uniform(0.5, 2.0, n)
    c = low + rng.uniform(0.0, (h - low))
    res = compute_arctan_full(o, h, low, c)
    assert isinstance(res, ArctanResult)
    assert res.raw_level_count.shape == (n,)
    assert res.level_count_clamped.shape == (n,)
    assert "up_329" in res.levels and "dn_329" in res.levels


def test_compute_arctan_full_clamps_to_sigma_329_band():
    # クランプ: level_count_clamped が [dn_329, up_329] に収まる。
    # 平坦なベースラインに 2 本の強い方向性バーを差し込み、ある点の raw が
    # ±3.29σ バンドを超える（= clip が効く）discriminating な入力を作る。
    rng = np.random.default_rng(0)
    n = 60
    base = 100.0
    o = np.full(n, base) + rng.normal(0, 0.05, n)
    h = o + 0.1
    low = o - 0.1
    c = o + rng.normal(0, 0.05, n)
    c[30] += 5.0
    h[30] += 5.0
    c[31] += 5.2
    h[31] += 5.2
    res = compute_arctan_full(o, h, low, c)
    upper = res.levels["up_329"]
    lower = res.levels["dn_329"]
    assert np.all(res.level_count_clamped <= upper + 1e-12)
    assert np.all(res.level_count_clamped >= lower - 1e-12)
    # discriminating: raw の最大/最小は band を超える（clip が効いている）
    assert res.raw_level_count.max() > upper or res.raw_level_count.min() < lower
    expected = np.clip(res.raw_level_count, lower, upper)
    np.testing.assert_allclose(res.level_count_clamped, expected, rtol=0, atol=0)


def test_compute_arctan_full_arrays_are_readonly():
    rng = np.random.default_rng(5)
    n = 20
    o = rng.uniform(100, 110, n)
    h = o + 1.0; low = o - 1.0; c = o + 0.2
    res = compute_arctan_full(o, h, low, c)
    assert res.raw_level_count.flags.writeable is False
    assert res.level_count_clamped.flags.writeable is False
    with pytest.raises(ValueError):
        res.raw_level_count[0] = 0.0


def test_compute_arctan_full_is_frozen_dto():
    rng = np.random.default_rng(9)
    n = 15
    o = rng.uniform(100, 110, n)
    h = o + 1.0; low = o - 1.0; c = o + 0.1
    res = compute_arctan_full(o, h, low, c)
    with pytest.raises(Exception):
        res.raw_level_count = np.zeros(n)  # type: ignore[misc]


def test_compute_arctan_full_ohlc_length_mismatch_raises():
    o = np.array([1.0, 2.0, 3.0])
    h = np.array([2.0, 3.0])  # 長さ不一致
    low = np.array([0.5, 1.0, 1.5])
    c = np.array([1.5, 2.5, 2.0])
    with pytest.raises(ValueError):
        compute_arctan_full(o, h, low, c)


def test_applied_prices_constant_is_seven_in_order():
    assert APPLIED_PRICES == ("W", "T", "M", "H", "L", "O", "C")


def test_sigma_levels_constant():
    assert SIGMA_LEVELS == (0.67, 1.28, 1.65, 1.96, 2.58, 3.29)
