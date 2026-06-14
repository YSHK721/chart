"""層名: core 層テスト（純粋計算・numpy）。

責務:
    PRO!fit_HLBand（overlay バンド・アンダースコア版）の core 層を元 MQL
    ``PRO!fit_HLBand.mq4`` と 1:1 再現で固定する。discriminating input により
    距離=|H-C|/|L-C|（L205-206 MathAbs）、band_upper=mean+dev·母σ（標本σ ddof=1
    では fail）、起点 close_ref=close[-2]（close[-1] では fail）、8 バンドの符号
    （up=close_ref+band_upper(dist_high), dn=close_ref-band_upper(dist_low)）を固定する。

元 MQL 対応:
    L205 ResBufferDivisionOpenHigh[i]=MathAbs(iHigh(i)-iClose(i))  → compute_distances dist_high
    L206 ResBufferDivisionOpenLow[i] =MathAbs(iLow(i)-iClose(i))   → compute_distances dist_low
    L220-223 iClose(1)+iBandsOnArray(OpenHigh,dev,0,1,0)           → band_upper / up_k（加算）
    L224-227 iClose(1)-iBandsOnArray(OpenLow,dev,0,1,0)            → dn_k（減算）
    iClose(...,1) = 系列 index 1 = 昇順 close[-2]                  → close_ref

依存: 標準 sys/pathlib / 外部 numpy, pytest / プロジェクト内 src.core
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import (  # noqa: E402
    HL_BAND_DEVS,
    HlBandResult,
    band_upper,
    compute_distances,
    compute_hl_band,
)

# === discriminating dataset（N=5） ===
# close[-2]=12.0 != close[-1]=14.0（起点 close_ref の区別）。
# dist_high != dist_low（up/dn の dist 対応の区別）。
# 母σ(÷N) != 標本σ(ddof=1)（band_upper の σ 種別の区別）。
_HIGH = np.array([10.0, 12.0, 11.0, 13.0, 15.0])
_LOW = np.array([8.0, 9.0, 9.5, 10.0, 11.0])
_CLOSE = np.array([9.0, 10.0, 10.5, 12.0, 14.0])

# 手計算: dist_high=|H-C|=[1.0,2.0,0.5,1.0,1.0]、dist_low=|L-C|=[1.0,1.0,1.0,2.0,3.0]
_EXPECTED_DIST_HIGH = np.array([1.0, 2.0, 0.5, 1.0, 1.0])
_EXPECTED_DIST_LOW = np.array([1.0, 1.0, 1.0, 2.0, 3.0])

# 手計算: mean(dist_low)=1.6, 母σ(dist_low)=0.8 → band_upper(dist_low,1.65)=1.6+1.65*0.8=2.92
# 手計算: mean(dist_high)=1.1, 母σ(dist_high)=0.4898979485566356
_CLOSE_REF = 12.0  # close[-2]


# --- compute_distances: 距離 = |H-C| / |L-C| ---
def test_compute_distances_returns_abs_high_close_and_low_close():
    # Arrange
    high, low, close = _HIGH, _LOW, _CLOSE
    # Act
    dist_high, dist_low = compute_distances(high, low, close)
    # Assert（手計算 abs。符号反転入力でも abs により正値固定）
    np.testing.assert_allclose(dist_high, _EXPECTED_DIST_HIGH)
    np.testing.assert_allclose(dist_low, _EXPECTED_DIST_LOW)


def test_compute_distances_takes_absolute_value_when_close_above_high():
    # Arrange（close > high, close < low となる入力で abs を強制検証）
    high = np.array([10.0, 10.0])
    low = np.array([5.0, 5.0])
    close = np.array([12.0, 3.0])  # |H-C|=[2,7], |L-C|=[7,2]
    # Act
    dist_high, dist_low = compute_distances(high, low, close)
    # Assert
    np.testing.assert_allclose(dist_high, [2.0, 7.0])
    np.testing.assert_allclose(dist_low, [7.0, 2.0])


# --- band_upper: mean + dev * 母σ(÷N)（標本σ ddof=1 では fail） ---
def test_band_upper_uses_population_std_not_sample_std():
    # Arrange（dist_low: mean=1.6, 母σ=0.8）
    dist = _EXPECTED_DIST_LOW
    # Act
    value = band_upper(dist, 1.65)
    # Assert（母σ: 1.6+1.65*0.8=2.92。標本σ(ddof=1)なら 2.92 にならない）
    assert value == pytest.approx(2.92, abs=1e-12)


def test_band_upper_scales_with_dev_population_sigma():
    # Arrange（dist_low: mean=1.6, 母σ=0.8）
    dist = _EXPECTED_DIST_LOW
    # Act / Assert（dev=2.58: 1.6+2.58*0.8=3.664）
    assert band_upper(dist, 2.58) == pytest.approx(3.664, abs=1e-12)


# --- HL_BAND_DEVS: 固定 dev 4 本 ---
def test_hl_band_devs_are_the_four_fixed_deviations():
    # Assert
    assert HL_BAND_DEVS == (0.67, 1.65, 1.96, 2.58)


# --- compute_hl_band: close_ref = close[-2]（close[-1] では fail） ---
def test_compute_hl_band_close_ref_is_second_to_last_close():
    # Act
    result = compute_hl_band(_HIGH, _LOW, _CLOSE)
    # Assert（close[-2]=12.0。close[-1]=14.0 を使うと fail）
    assert result.close_ref == pytest.approx(12.0, abs=1e-12)


def test_compute_hl_band_close_ref_changes_with_second_to_last_close():
    # Arrange（末尾2本 close を変えて close[-2] 追従を区別。close[-1] 使用なら不変）
    close = np.array([9.0, 10.0, 10.5, 20.0, 14.0])  # close[-2]=20.0
    # Act
    result = compute_hl_band(_HIGH, _LOW, close)
    # Assert
    assert result.close_ref == pytest.approx(20.0, abs=1e-12)


# --- compute_hl_band: 8 バンド符号・dist 対応・dev 対応 ---
def test_compute_hl_band_upper_bands_add_band_upper_of_dist_high():
    # Act（後方互換モード = 全長・絶対距離。旧 core.py:146-147 の固定点）
    result = compute_hl_band(_HIGH, _LOW, _CLOSE, window=None, normalize=False)
    # Assert（up_k = close_ref + band_upper(dist_high, dev_k)・加算）
    levels = result.levels
    assert levels["up_067"] == pytest.approx(12.0 + band_upper(_EXPECTED_DIST_HIGH, 0.67), abs=1e-12)
    assert levels["up_165"] == pytest.approx(12.0 + band_upper(_EXPECTED_DIST_HIGH, 1.65), abs=1e-12)
    assert levels["up_196"] == pytest.approx(12.0 + band_upper(_EXPECTED_DIST_HIGH, 1.96), abs=1e-12)
    assert levels["up_258"] == pytest.approx(12.0 + band_upper(_EXPECTED_DIST_HIGH, 2.58), abs=1e-12)


def test_compute_hl_band_lower_bands_subtract_band_upper_of_dist_low():
    # Act（後方互換モード = 全長・絶対距離。旧 core.py:146-147 の固定点）
    result = compute_hl_band(_HIGH, _LOW, _CLOSE, window=None, normalize=False)
    # Assert（dn_k = close_ref - band_upper(dist_low, dev_k)・減算。手計算: dn_165=12.0-2.92=9.08）
    levels = result.levels
    assert levels["dn_067"] == pytest.approx(12.0 - band_upper(_EXPECTED_DIST_LOW, 0.67), abs=1e-12)
    assert levels["dn_165"] == pytest.approx(9.08, abs=1e-12)
    assert levels["dn_196"] == pytest.approx(12.0 - band_upper(_EXPECTED_DIST_LOW, 1.96), abs=1e-12)
    assert levels["dn_258"] == pytest.approx(12.0 - band_upper(_EXPECTED_DIST_LOW, 2.58), abs=1e-12)


def test_compute_hl_band_levels_has_exactly_eight_keys():
    # Act
    result = compute_hl_band(_HIGH, _LOW, _CLOSE)
    # Assert
    assert set(result.levels.keys()) == {
        "up_067", "up_165", "up_196", "up_258",
        "dn_067", "dn_165", "dn_196", "dn_258",
    }


def test_compute_hl_band_exposes_distances():
    # Act（後方互換モード = 絶対距離を dist_* として保持する固定点）
    result = compute_hl_band(_HIGH, _LOW, _CLOSE, window=None, normalize=False)
    # Assert
    np.testing.assert_allclose(result.dist_high, _EXPECTED_DIST_HIGH)
    np.testing.assert_allclose(result.dist_low, _EXPECTED_DIST_LOW)


# --- 例外 ---
def test_compute_hl_band_raises_when_fewer_than_two_bars():
    # Arrange（N<2 → close[-2] 不在）
    high = np.array([10.0])
    low = np.array([8.0])
    close = np.array([9.0])
    # Act / Assert
    with pytest.raises(ValueError):
        compute_hl_band(high, low, close)


def test_compute_distances_raises_on_length_mismatch():
    # Arrange
    high = np.array([10.0, 12.0])
    low = np.array([8.0, 9.0])
    close = np.array([9.0])  # 長さ不一致
    # Act / Assert
    with pytest.raises(ValueError):
        compute_distances(high, low, close)


def test_compute_hl_band_raises_on_length_mismatch():
    # Arrange
    high = np.array([10.0, 12.0, 11.0])
    low = np.array([8.0, 9.0])
    close = np.array([9.0, 10.0, 10.5])
    # Act / Assert
    with pytest.raises(ValueError):
        compute_hl_band(high, low, close)


# --- DTO 不変性 ---
def test_hl_band_result_is_frozen():
    # Arrange
    result = compute_hl_band(_HIGH, _LOW, _CLOSE)
    # Act / Assert（frozen dataclass: 属性代入で FrozenInstanceError）
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.close_ref = 0.0  # type: ignore[misc]


def test_hl_band_result_distances_are_read_only():
    # Arrange
    result = compute_hl_band(_HIGH, _LOW, _CLOSE)
    # Act / Assert（writeable=False → 書込で ValueError）
    with pytest.raises(ValueError):
        result.dist_high[0] = 999.0
    with pytest.raises(ValueError):
        result.dist_low[0] = 999.0


def test_hl_band_result_type():
    # Act
    result = compute_hl_band(_HIGH, _LOW, _CLOSE)
    # Assert
    assert isinstance(result, HlBandResult)
