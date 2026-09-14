"""TDD: domain/variance_forecast.py（詳細設計 §3.2 / §9.1）。"""
from __future__ import annotations

import math

import pytest

from simulator.domain.variance_forecast import VarianceForecast


class TestVarianceForecastValid:
    def test_estimable_with_positive_sigmas(self):
        fc = VarianceForecast("2024-W07", 0.025, 0.020, 0.018, estimable=True)
        assert fc.sigma_plus == 0.025
        assert fc.sigma_minus == 0.020
        assert fc.estimable is True

    def test_no_trade_factory_sets_estimable_false(self):
        fc = VarianceForecast.no_trade("2024-W07", sigma_total_prev=0.018)
        assert fc.estimable is False
        assert fc.sigma_plus is None
        assert fc.sigma_minus is None
        assert fc.sigma_total_prev == 0.018

    def test_no_trade_allows_none_total_prev(self):
        fc = VarianceForecast.no_trade("2024-W07")
        assert fc.estimable is False
        assert fc.sigma_total_prev is None


class TestVarianceForecastInvalid:
    def test_estimable_with_none_sigma_plus_raises(self):
        with pytest.raises(ValueError):
            VarianceForecast("2024-W07", None, 0.020, 0.018, estimable=True)

    def test_estimable_with_zero_sigma_minus_raises(self):
        with pytest.raises(ValueError):
            VarianceForecast("2024-W07", 0.025, 0.0, 0.018, estimable=True)

    def test_estimable_with_negative_sigma_raises(self):
        with pytest.raises(ValueError):
            VarianceForecast("2024-W07", -0.01, 0.020, 0.018, estimable=True)

    def test_estimable_with_inf_sigma_raises(self):
        with pytest.raises(ValueError):
            VarianceForecast("2024-W07", math.inf, 0.020, 0.018, estimable=True)
