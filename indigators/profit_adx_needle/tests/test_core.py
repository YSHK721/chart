"""PRO!fit_ADX_NEEDLE コア計算の検証。

元 MQL4 / PS.mqh の挙動（ADX の EMA 平滑・単位変換の符号・σ バンド・クランプ）を、
手計算可能な小入力で固定する（ガイド §7）。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    SIGMA_LEVELS,
    compute_adx,
    compute_adx_needle,
    compute_level_count,
    compute_sigma_levels,
    ps_level_count,
)
from src.core import _ema, _ps_average, _ps_std_ema, _unit_conversion  # noqa: E402
from src.needle import build_adx_needle, needle_levels  # noqa: E402

import pandas as pd  # noqa: E402


# --------------------------------------------------------------------------- EMA
def test_ema_matches_recurrence():
    # alpha=2/3。手計算: 1, 1.6667, 2.5556。
    out = _ema(np.array([1.0, 2.0, 3.0]), period=2)
    assert out[0] == pytest.approx(1.0)
    assert out[1] == pytest.approx(1.0 + (2 / 3) * 1.0)
    assert out[2] == pytest.approx(out[1] + (2 / 3) * (3.0 - out[1]))


def test_ema_empty():
    assert _ema(np.array([]), period=5).size == 0


# --------------------------------------------------------------------------- ADX
def _uptrend(n=60):
    high = np.arange(n, dtype=float) + 10.0
    low = high - 1.0
    close = high - 0.5
    return high, low, close


def test_adx_in_unit_range():
    h, low, c = _uptrend()
    adx = compute_adx(h, low, c, period=6)
    assert adx.shape == (60,)
    assert np.all(adx >= 0.0) and np.all(adx <= 100.0)


def test_adx_pure_uptrend_approaches_100():
    # 単調上昇では +DM のみ → DX=100 が続き ADX→100。
    h, low, c = _uptrend(80)
    adx = compute_adx(h, low, c, period=6)
    assert adx[-1] > 90.0


def test_adx_flat_is_zero():
    # 値動きなし → DM=TR=0 → ADX=0。
    flat = np.full(30, 5.0)
    adx = compute_adx(flat, flat, flat, period=6)
    assert np.allclose(adx, 0.0)


def test_adx_length_mismatch_raises():
    with pytest.raises(ValueError):
        compute_adx(np.zeros(5), np.zeros(4), np.zeros(5))


def test_adx_empty_raises():
    with pytest.raises(ValueError):
        compute_adx(np.array([]), np.array([]), np.array([]))


def test_adx_bad_period_raises():
    with pytest.raises(ValueError):
        compute_adx(np.zeros(5), np.zeros(5), np.zeros(5), period=0)


# ----------------------------------------------------------------- unit conversion
def test_unit_conversion_sign_symmetry():
    # 両分岐とも (osi-avg)/std に帰着（band=avg±std*sigma, distant=329, sigma=3.29）。
    avg, std, sigma, distant = 3.0, 1.5, 3.29, 329.0
    up = avg + std * sigma
    down = avg - std * sigma
    above = _unit_conversion(5.0, avg, up, distant, 1)    # UPSIDE
    below = _unit_conversion(1.0, avg, down, distant, 2)  # DOWNSIDE
    assert above == pytest.approx((5.0 - avg) / std, abs=1e-4)
    assert below == pytest.approx((1.0 - avg) / std, abs=1e-4)  # 負
    assert above > 0 and below < 0


def test_unit_conversion_zero_length():
    # band==avg（std=0 相当）→ length=0 → 0。
    assert _unit_conversion(5.0, 3.0, 3.0, 329.0, 1) == 0.0


# --------------------------------------------------------------------- level count
def test_level_count_doji_and_sign():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])  # avg=3
    avg = _ps_average(x)
    std = _ps_std_ema(x)
    lc = ps_level_count(x, initialization=True)
    # 平均(=3)に一致する要素は 0。
    assert lc[2] == pytest.approx(0.0)
    # 平均超は正、平均未満は負、(x-avg)/std に一致。
    assert lc[4] == pytest.approx((5.0 - avg) / std, abs=1e-4)
    assert lc[0] == pytest.approx((1.0 - avg) / std, abs=1e-4)
    assert lc[0] < 0 < lc[4]


def test_compute_level_count_is_sevenfold():
    h, low, c = _uptrend(50)
    adx = compute_adx(h, low, c, period=6)
    single = ps_level_count(adx, initialization=True)
    seven = compute_level_count(h, low, c, period=6)
    # 7 系統の適用価格 ADX は同一 → 単一の 7 倍（MetaQuotes 仕様 / SPEC §9）。
    assert np.allclose(seven, 7.0 * single)


def test_level_count_flat_is_zero():
    flat = np.full(20, 5.0)
    lc = compute_level_count(flat, flat, flat, period=6)
    assert np.allclose(lc, 0.0)


# --------------------------------------------------------------------- sigma levels
def test_sigma_levels_exact():
    x = np.array([0.0, 2.0, 4.0])  # mean=2, popstd=sqrt(8/3)
    mean = 2.0
    std = float(np.sqrt(8.0 / 3.0))
    levels = compute_sigma_levels(x)
    for sigma in SIGMA_LEVELS:
        key = f"{int(round(sigma * 100)):03d}"
        assert levels[f"up_{key}"] == pytest.approx(round(mean + std * sigma, 5))
        assert levels[f"dn_{key}"] == pytest.approx(round(mean - std * sigma, 5))


# ------------------------------------------------------------------------- needle
def test_needle_clamped_within_bounds():
    h, low, c = _uptrend(70)
    res = compute_adx_needle(h, low, c, period=6)
    assert np.all(res.needle >= res.lower_clamp - 1e-9)
    assert np.all(res.needle <= res.upper_clamp + 1e-9)
    # クランプ境界 = ±3.29σ 水準。
    assert res.upper_clamp == pytest.approx(res.sigma_levels["up_329"])
    assert res.lower_clamp == pytest.approx(res.sigma_levels["dn_329"])


def test_needle_equals_level_when_within_bounds():
    h, low, c = _uptrend(70)
    res = compute_adx_needle(h, low, c, period=6)
    inside = (res.level_count >= res.lower_clamp) & (res.level_count <= res.upper_clamp)
    assert np.allclose(res.needle[inside], res.level_count[inside])


def test_result_is_immutable():
    h, low, c = _uptrend(30)
    res = compute_adx_needle(h, low, c, period=6)
    with pytest.raises(ValueError):
        res.needle[0] = 999.0


# --------------------------------------------------------------------- needle layer
def _df(n=60):
    h = np.arange(n, dtype=float) + 10.0
    return pd.DataFrame({"high": h, "low": h - 1.0, "close": h - 0.5})


def test_build_adx_needle_columns():
    out = build_adx_needle(_df(), period=6)
    assert list(out.columns) == ["adx_needle", "adx_level_count", "adx"]
    assert len(out) == 60


def test_build_adx_needle_missing_column_raises():
    bad = pd.DataFrame({"high": [1.0], "low": [0.5]})  # close 欠落
    with pytest.raises(KeyError):
        build_adx_needle(bad)


def test_needle_levels_has_clamp_bounds():
    levels = needle_levels(_df(), period=6)
    assert "upper_clamp" in levels and "lower_clamp" in levels
    assert levels["upper_clamp"] == pytest.approx(levels["up_329"])
