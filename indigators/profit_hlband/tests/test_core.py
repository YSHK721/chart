"""層名: core 層テスト（純粋計算・numpy）。

責務:
    PRO!fitHLBand の core 層（range / range_stats / hl_bands / hlband 統合 DTO）を
    元 MQL ``PRO!fitHLBand.mq4`` と 1:1 再現で固定する。discriminating input により
    母σ÷N（標本σ ddof=1 では fail）・overlay 8 本の符号（High 減算 / Low 加算）・
    最新 H/L（昇順 last = high[-1]、high[0] では fail）・sub_max=b196*2 を固定する。

元 MQL 対応:
    L61 ExtVOLBuffer[i]=high[i]-low[i]            → compute_range
    L67-74 iMAOnArray/iBandsOnArray(...母σ÷N...)  → compute_range_stats
    L67-74 iHigh(0)-... / iLow(0)+...            → compute_hl_bands（High 減算 / Low 加算）
    L102 INDICATOR_MINIMUM=0                      → HLBandResult.sub_min
    L103 INDICATOR_MAXIMUM=StcLCStdDevArray[2]*2  → HLBandResult.sub_max（b196*2）

依存: 標準 sys/pathlib / 外部 numpy, pytest / プロジェクト内 src.core
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import (  # noqa: E402
    HLBandResult,
    HLPriceBands,
    RangeStats,
    compute_hl_bands,
    compute_hlband,
    compute_range,
    compute_range_stats,
)

# discriminating input（母σ÷N と標本σ ddof=1 で値が異なる／high[0]!=high[-1]）。
#   range = [2,3,3,3], avg=2.75, 母σ=sqrt(0.1875)=0.4330127..., 標本σ=0.5
#   H_last=high[-1]=13.0, L_last=low[-1]=10.0（high[0]=10, low[0]=8 とは異なる）
_HIGH = np.array([10.0, 12.0, 14.0, 13.0])
_LOW = np.array([8.0, 9.0, 11.0, 10.0])

_AVG = 2.75
_SIGMA_POP = float(np.sqrt(0.1875))  # 0.4330127018922193（母σ÷N）
_B165 = _AVG + 1.65 * _SIGMA_POP
_B196 = _AVG + 1.96 * _SIGMA_POP
_B258 = _AVG + 2.58 * _SIGMA_POP
_H_LAST = 13.0
_L_LAST = 10.0


# --- compute_range ---------------------------------------------------------


def test_compute_range_equals_high_minus_low_for_all_indices() -> None:
    # Arrange
    high = _HIGH
    low = _LOW
    # Act
    result = compute_range(high, low)
    # Assert: range[i] = high[i]-low[i] 全 i（warm-up なし・NaN なし）
    np.testing.assert_array_equal(result, np.array([2.0, 3.0, 3.0, 3.0]))


def test_compute_range_raises_valueerror_when_high_low_length_mismatch() -> None:
    # Arrange
    high = np.array([10.0, 12.0, 14.0])
    low = np.array([8.0, 9.0])
    # Act / Assert
    with pytest.raises(ValueError):
        compute_range(high, low)


# --- compute_range_stats ---------------------------------------------------


def test_compute_range_stats_avg_is_full_series_mean() -> None:
    # Arrange
    range_ = compute_range(_HIGH, _LOW)
    # Act
    stats = compute_range_stats(range_)
    # Assert
    assert stats.avg == pytest.approx(_AVG)


def test_compute_range_stats_sigma_is_population_divided_by_n() -> None:
    # Arrange: 母σ÷N を固定。標本σ(ddof=1)=0.5 実装なら fail する discriminating input。
    range_ = compute_range(_HIGH, _LOW)
    # Act
    stats = compute_range_stats(range_)
    # Assert
    assert stats.sigma == pytest.approx(_SIGMA_POP)
    assert stats.sigma != pytest.approx(0.5)  # 標本σ(ddof=1) では fail する


def test_compute_range_stats_three_bands_are_avg_plus_k_sigma() -> None:
    # Arrange
    range_ = compute_range(_HIGH, _LOW)
    # Act
    stats = compute_range_stats(range_)
    # Assert: b165/b196/b258 = avg + {1.65,1.96,2.58}*母σ
    assert stats.b165 == pytest.approx(_B165)
    assert stats.b196 == pytest.approx(_B196)
    assert stats.b258 == pytest.approx(_B258)


def test_range_stats_is_frozen_dto() -> None:
    # Arrange
    stats = compute_range_stats(compute_range(_HIGH, _LOW))
    # Act / Assert: frozen DTO は再代入不可
    with pytest.raises(Exception):
        stats.avg = 0.0  # type: ignore[misc]


# --- compute_hl_bands （overlay 8 本・符号） --------------------------------


def test_compute_hl_bands_high_side_subtracts_from_latest_high() -> None:
    # Arrange: H_last=high[-1]=13.0。high[0]=10.0 を使うと fail する discriminating input。
    stats = compute_range_stats(compute_range(_HIGH, _LOW))
    # Act
    bands = compute_hl_bands(_HIGH, _LOW, stats)
    # Assert: High 側 = 減算（H_last から下へ投影）
    assert bands.high_avg == pytest.approx(_H_LAST - _AVG)
    assert bands.high_b165 == pytest.approx(_H_LAST - _B165)
    assert bands.high_b196 == pytest.approx(_H_LAST - _B196)
    assert bands.high_b258 == pytest.approx(_H_LAST - _B258)


def test_compute_hl_bands_low_side_adds_to_latest_low() -> None:
    # Arrange: L_last=low[-1]=10.0。low[0]=8.0 を使うと fail する discriminating input。
    stats = compute_range_stats(compute_range(_HIGH, _LOW))
    # Act
    bands = compute_hl_bands(_HIGH, _LOW, stats)
    # Assert: Low 側 = 加算（L_last から上へ投影）
    assert bands.low_avg == pytest.approx(_L_LAST + _AVG)
    assert bands.low_b165 == pytest.approx(_L_LAST + _B165)
    assert bands.low_b196 == pytest.approx(_L_LAST + _B196)
    assert bands.low_b258 == pytest.approx(_L_LAST + _B258)


def test_compute_hl_bands_uses_last_not_first_element() -> None:
    # Arrange: high[0]=10!=high[-1]=13、low[0]=8!=low[-1]=10。
    # high[0]/low[0] を使う実装なら high_avg=10-2.75=7.25 となり以下が fail する。
    stats = compute_range_stats(compute_range(_HIGH, _LOW))
    # Act
    bands = compute_hl_bands(_HIGH, _LOW, stats)
    # Assert
    assert bands.high_avg == pytest.approx(13.0 - 2.75)  # 10.25（昇順 last）
    assert bands.high_avg != pytest.approx(10.0 - 2.75)  # 7.25（先頭ではない）
    assert bands.low_avg == pytest.approx(10.0 + 2.75)  # 12.75（昇順 last）
    assert bands.low_avg != pytest.approx(8.0 + 2.75)  # 10.75（先頭ではない）


def test_hl_price_bands_is_frozen_dto() -> None:
    # Arrange
    stats = compute_range_stats(compute_range(_HIGH, _LOW))
    bands = compute_hl_bands(_HIGH, _LOW, stats)
    # Act / Assert
    with pytest.raises(Exception):
        bands.high_avg = 0.0  # type: ignore[misc]


# --- compute_hlband （統合 DTO） -------------------------------------------


def test_compute_hlband_range_field_matches_compute_range() -> None:
    # Arrange / Act
    result = compute_hlband(_HIGH, _LOW)
    # Assert
    np.testing.assert_array_equal(result.range, np.array([2.0, 3.0, 3.0, 3.0]))


def test_compute_hlband_range_is_not_writeable() -> None:
    # Arrange / Act
    result = compute_hlband(_HIGH, _LOW)
    # Assert: DTO 不変性（range は writeable=False）
    assert result.range.flags.writeable is False


def test_compute_hlband_sub_min_is_zero() -> None:
    # Arrange / Act
    result = compute_hlband(_HIGH, _LOW)
    # Assert: sub_min=0.0（元 INDICATOR_MINIMUM=0）
    assert result.sub_min == 0.0


def test_compute_hlband_sub_max_is_b196_times_two() -> None:
    # Arrange / Act
    result = compute_hlband(_HIGH, _LOW)
    # Assert: sub_max=b196*2（元 INDICATOR_MAXIMUM=StcLCStdDevArray[2]*2）。
    #   b258*2 等の誤りなら fail する。
    assert result.sub_max == pytest.approx(_B196 * 2)
    assert result.sub_max != pytest.approx(_B258 * 2)  # b258*2 では fail


def test_compute_hlband_aggregates_stats_and_bands() -> None:
    # Arrange / Act
    result = compute_hlband(_HIGH, _LOW)
    # Assert: 統合 DTO が stats / bands を保持
    assert isinstance(result.stats, RangeStats)
    assert isinstance(result.bands, HLPriceBands)
    assert result.stats.avg == pytest.approx(_AVG)
    assert result.bands.high_avg == pytest.approx(_H_LAST - _AVG)


def test_compute_hlband_returns_frozen_hlbandresult() -> None:
    # Arrange / Act
    result = compute_hlband(_HIGH, _LOW)
    # Assert
    assert isinstance(result, HLBandResult)
    with pytest.raises(Exception):
        result.sub_min = 1.0  # type: ignore[misc]


def test_compute_hlband_raises_valueerror_when_length_mismatch() -> None:
    # Arrange
    high = np.array([10.0, 12.0, 14.0])
    low = np.array([8.0, 9.0])
    # Act / Assert
    with pytest.raises(ValueError):
        compute_hlband(high, low)
