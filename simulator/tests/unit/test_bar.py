"""E-Bar の単体テスト（CLEAN_ARCH §4 / DESIGN §3）。

不変条件: low <= min(open,close) <= max(open,close) <= high かつ spread >= 0。
違反時 OHLCInvalidError。振る舞いなし（不変データ）。
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from simulator.domain.bar import Bar
from simulator.domain.exceptions import OHLCInvalidError


def _t():
    return np.datetime64("2024-01-01T00:00:00")


class TestBarValid:
    def test_valid_ohlc_constructs(self):
        # Arrange / Act
        bar = Bar(time=_t(), open=1.0, high=1.5, low=0.8, close=1.2, volume=10.0, spread=2)
        # Assert
        assert bar.open == 1.0
        assert bar.high == 1.5
        assert bar.low == 0.8
        assert bar.close == 1.2

    def test_flat_bar_boundary_is_valid(self):
        # Arrange / Act: low == open == close == high（縮退ケース・境界）
        bar = Bar(time=_t(), open=1.0, high=1.0, low=1.0, close=1.0, volume=0.0, spread=0)
        # Assert
        assert bar.high == bar.low == 1.0

    def test_spread_zero_boundary_is_valid(self):
        # Arrange / Act: spread == 0（下限境界）
        bar = Bar(time=_t(), open=1.0, high=1.5, low=0.8, close=1.2, volume=1.0, spread=0)
        # Assert
        assert bar.spread == 0

    def test_epoch_int_time_is_accepted(self):
        # Arrange / Act: 時刻型は epoch int も許容（pd.Timestamp 禁止方針）
        bar = Bar(time=1_700_000_000, open=1.0, high=1.5, low=0.8, close=1.2, volume=1.0, spread=1)
        # Assert
        assert bar.time == 1_700_000_000

    def test_bar_is_frozen(self):
        # Arrange
        bar = Bar(time=_t(), open=1.0, high=1.5, low=0.8, close=1.2, volume=1.0, spread=1)
        # Act / Assert: frozen dataclass は再代入不可
        with pytest.raises(dataclasses.FrozenInstanceError):
            bar.open = 2.0  # type: ignore[misc]


class TestBarInvalid:
    def test_low_above_open_raises(self):
        # low > min(open, close)
        with pytest.raises(OHLCInvalidError):
            Bar(time=_t(), open=1.0, high=1.5, low=1.1, close=1.2, volume=1.0, spread=1)

    def test_high_below_close_raises(self):
        # max(open, close) > high
        with pytest.raises(OHLCInvalidError):
            Bar(time=_t(), open=1.0, high=1.1, low=0.8, close=1.2, volume=1.0, spread=1)

    def test_high_below_low_raises(self):
        # high < low（総合矛盾）
        with pytest.raises(OHLCInvalidError):
            Bar(time=_t(), open=1.0, high=0.5, low=0.8, close=0.9, volume=1.0, spread=1)

    def test_negative_spread_raises(self):
        # spread < 0（境界の外）
        with pytest.raises(OHLCInvalidError):
            Bar(time=_t(), open=1.0, high=1.5, low=0.8, close=1.2, volume=1.0, spread=-1)
