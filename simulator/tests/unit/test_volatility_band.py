"""TDD: domain/volatility_band.py（詳細設計 §3.3 / §9.1・NFR-D1 数値例 oracle）。"""
from __future__ import annotations

import pytest

from simulator.domain.volatility_band import VolatilityBand


class TestVolatilityBandNumericExample:
    def test_spec_numeric_example_S_T_N(self):
        # 仕様 §数値例: O=39000, σ̂⁻=0.020, σ̂⁺=0.025, p_tp=0.50,
        # Capital=1,000,000, f_risk=0.01 → S≈37501, T≈39663, N≈6.67
        band = VolatilityBand.from_forecast(
            week_id="2024-W07",
            O=39000.0,
            sigma_minus=0.020,
            sigma_plus=0.025,
            p_tp=0.50,
            f_risk=0.01,
            capital=1_000_000.0,
        )
        assert round(band.S, 0) == 37501.0
        assert round(band.T, 0) == 39663.0
        assert round(band.N, 2) == 6.67


class TestVolatilityBandInvariants:
    def test_O_minus_S_le_zero_raises(self):
        with pytest.raises(ValueError):
            VolatilityBand("w", O=100.0, S=100.0, T=120.0, N=1.0, p_tp=0.50)

    def test_S_le_zero_raises(self):
        with pytest.raises(ValueError):
            VolatilityBand("w", O=100.0, S=0.0, T=120.0, N=1.0, p_tp=0.50)

    def test_T_le_O_raises(self):
        with pytest.raises(ValueError):
            VolatilityBand("w", O=100.0, S=90.0, T=100.0, N=1.0, p_tp=0.50)

    def test_valid_band_constructs(self):
        band = VolatilityBand("w", O=100.0, S=90.0, T=110.0, N=1.0, p_tp=0.50)
        assert band.O == 100.0


class TestVolatilityBandFromForecastGuards:
    def test_p_tp_off_grid_raises(self):
        with pytest.raises(ValueError):
            VolatilityBand.from_forecast(
                week_id="w", O=39000.0, sigma_minus=0.02, sigma_plus=0.025,
                p_tp=0.55, f_risk=0.01, capital=1e6,
            )

    def test_p_tp_grid_070_constructs(self):
        # 境界：グリッド端 0.70（z=0.385）
        band = VolatilityBand.from_forecast(
            week_id="w", O=39000.0, sigma_minus=0.02, sigma_plus=0.025,
            p_tp=0.70, f_risk=0.01, capital=1e6,
        )
        assert band.T > band.O
