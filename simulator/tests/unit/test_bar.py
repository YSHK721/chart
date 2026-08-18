"""E-Bar の単体テスト（CLEAN_ARCH §4 / DESIGN §3）。

不変条件: low <= min(open,close) <= max(open,close) <= high かつ spread >= 0。
違反時 OHLCInvalidError。振る舞いなし（不変データ）。
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from simulator.domain.bar import Bar
from simulator.domain.exceptions import ConfigError, OHLCInvalidError


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


# ============================================================================
# ISSUE-411 スライス 3: time の型契約を構築時に表明する（ISSUE-403 残スライス）
# ============================================================================
#
# 未対応の時刻表現の `Bar` を**そもそも作らせない**ことで、下流の手書き型判定
# （ISSUE-412 の同型サイト群）が必要になる原因そのものを消す。受理集合の定義は
# `bar_time.EPOCH_CONVERTERS` が唯一持ち、ここでは述語 `is_supported_time` を呼ぶ。


def _bar(time):
    """OHLC 不変条件を満たす引数で `Bar` を構築する（観測対象は time 契約のみ）。"""
    return Bar(time=time, open=1.0, high=1.5, low=0.8, close=1.2, volume=1.0, spread=1)


class TestBarTimeContract:
    @pytest.mark.parametrize(
        "time",
        [
            1_700_000_000,
            np.int64(1_700_000_000),
            np.datetime64("2024-01-01T00:00:00"),
            datetime(2024, 1, 1),
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            pd.Timestamp("2024-01-01"),
        ],
        ids=["int", "np.int64", "np.datetime64", "naive datetime", "aware datetime",
             "pd.Timestamp"],
    )
    def test_supported_time_representations_construct(self, time):
        # Arrange / Act: 受理集合（EPOCH_CONVERTERS）の表現は構築できる
        bar = _bar(time)
        # Assert
        assert bar.time is time

    @pytest.mark.parametrize(
        "time",
        ["2024-01-01T00:00:00", "1700000000", 1.5, 1_700_000_000.0, True, False, None,
         object()],
        ids=["ISO 文字列", "数字文字列", "float", "float epoch", "True", "False", "None",
             "object"],
    )
    def test_unsupported_time_representation_raises_config_error(self, time):
        # Arrange / Act / Assert: 表外の表現は推測で解釈せず fail-stop
        with pytest.raises(ConfigError):
            _bar(time)

    def test_config_error_carries_the_offending_type_in_context(self):
        # Arrange / Act
        with pytest.raises(ConfigError) as exc:
            _bar("2024-01-01T00:00:00")
        # Assert: 原因を無音にしない（type と value を context に載せる）
        assert exc.value.context["value_type"] == "str"
        assert exc.value.context["value"] == "2024-01-01T00:00:00"

    def test_time_contract_is_checked_before_the_ohlc_invariant(self):
        # Arrange: time 違反と OHLC 違反が同時に成立する引数
        # Act / Assert: 先に評価されるのは time 契約（ConfigError）であり OHLCInvalidError ではない
        with pytest.raises(ConfigError):
            Bar(time="2024-01-01T00:00:00", open=1.0, high=0.5, low=0.8, close=0.9,
                volume=1.0, spread=1)

    def test_time_contract_is_checked_before_the_spread_invariant(self):
        # Arrange: time 違反と spread 違反が同時に成立する引数
        # Act / Assert
        with pytest.raises(ConfigError):
            Bar(time="2024-01-01T00:00:00", open=1.0, high=1.5, low=0.8, close=1.2,
                volume=1.0, spread=-1)

    def test_ohlc_invariant_still_raises_when_time_is_valid(self):
        # Arrange / Act / Assert: time が契約内なら従来どおり OHLC 違反を送出する（退行防止）
        with pytest.raises(OHLCInvalidError):
            Bar(time=_t(), open=1.0, high=0.5, low=0.8, close=0.9, volume=1.0, spread=1)

    def test_accepted_set_is_not_enumerated_a_second_time_in_bar(self):
        """受理集合の定義は `bar_time` が唯一持つ（`bar.py` は述語を呼ぶだけ）。

        識別力: `bar.py` 側で型を列挙し直すと、表への追加に追随せず本検定が落ちる。
        """
        import simulator.domain.bar as bar_module
        import simulator.domain.bar_time as bar_time_module

        class _Marker:
            pass

        original = bar_time_module.EPOCH_CONVERTERS
        bar_time_module.EPOCH_CONVERTERS = original + (
            (lambda v: isinstance(v, _Marker), lambda v: 0),
        )
        try:
            assert bar_module.Bar(
                time=_Marker(), open=1.0, high=1.5, low=0.8, close=1.2,
                volume=1.0, spread=1,
            ).time is not None
        finally:
            bar_time_module.EPOCH_CONVERTERS = original
