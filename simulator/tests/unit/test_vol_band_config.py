"""TDD: framework/config_loader.py 追記（詳細設計 §11）。

VolEstimationParams / ValidationParams（pydantic・既定値・extra forbid）。
"""
from __future__ import annotations

import pytest

from simulator.framework.config_loader import ValidationParams, VolEstimationParams


class TestVolEstimationParams:
    def test_defaults(self):
        p = VolEstimationParams(min_bars_per_week=50)
        assert p.window == 260
        assert p.nw_lag == 4
        assert p.min_bars_per_week == 50

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            VolEstimationParams(min_bars_per_week=50, unknown=1)


class TestValidationParams:
    def test_defaults(self):
        p = ValidationParams()
        assert p.seed == 0
        assert p.B == 5000
        assert p.alpha_stop == 0.05
        assert p.f_risk == 0.01
        assert p.min_weeks == 260
        assert p.min_stop_hits == 30
        assert p.c_spread == 0.0
        assert p.e_grid == ["E0", "E1(0.5)", "E1(1.0)", "E1(1.5)", "E1(2.0)"]
        assert p.p_tp_grid == [0.40, 0.50, 0.60, 0.70]

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            ValidationParams(bogus=1)
