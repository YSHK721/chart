"""E-Exceptions の単体テスト（BacktestError 階層 / DESIGN §9.1）。

階層の isinstance 関係・共通属性 context を検証する。
"""
from __future__ import annotations

import numpy as np
import pytest

from backtest.domain.exceptions import (
    BacktestError,
    ConfigError,
    DataError,
    MissingBarError,
    OHLCInvalidError,
    TimeOrderError,
    IndicatorError,
    IndicatorNaNError,
    IndicatorBufferError,
    ExecutionError,
    InvalidPriceError,
    MarginCallError,
)


class TestBacktestErrorContext:
    def test_context_defaults_to_empty_dict_when_omitted(self):
        # Arrange / Act
        err = BacktestError("boom")
        # Assert: context は省略時 None ではなく空 dict
        assert err.context == {}

    def test_context_retains_supplied_mapping(self):
        # Arrange
        ctx = {"bar_index": 5, "symbol": "USDJPY"}
        # Act
        err = BacktestError("boom", context=ctx)
        # Assert
        assert err.context == ctx

    def test_message_is_accessible_via_str(self):
        # Arrange / Act
        err = BacktestError("boom")
        # Assert
        assert str(err) == "boom"


class TestBacktestErrorDiagnosticAttributes:
    # DESIGN §9.2/§9.3: 診断用に timestamp / symbol / bar_index / context の 4 属性（🟡-1）
    def test_diagnostic_attributes_default_to_none_when_omitted(self):
        # Arrange / Act: context 以外の 3 属性は省略時 None
        err = BacktestError("boom")
        # Assert
        assert err.symbol is None
        assert err.bar_index is None
        assert err.timestamp is None

    def test_diagnostic_attributes_retain_supplied_values(self):
        # Arrange
        ts = np.datetime64("2024-01-01T00:00")
        # Act
        err = BacktestError("boom", symbol="USDJPY", bar_index=42, timestamp=ts)
        # Assert: 4 属性が保持される
        assert err.symbol == "USDJPY"
        assert err.bar_index == 42
        assert err.timestamp == ts
        assert err.context == {}

    def test_timestamp_accepts_int_epoch(self):
        # 型は numpy.datetime64 | int | None（pd.Timestamp 禁止）
        err = BacktestError("boom", timestamp=1_700_000_000)
        assert err.timestamp == 1_700_000_000

    def test_all_attributes_coexist(self):
        # 4 属性が同時に保持される
        err = BacktestError(
            "boom",
            symbol="EURUSD",
            bar_index=7,
            timestamp=np.datetime64("2024-06-01"),
            context={"k": "v"},
        )
        assert err.symbol == "EURUSD"
        assert err.bar_index == 7
        assert err.timestamp == np.datetime64("2024-06-01")
        assert err.context == {"k": "v"}


class TestExceptionHierarchy:
    # DESIGN §9.1: BacktestError を基底に 4 系統 + 葉
    @pytest.mark.parametrize(
        "leaf, parent",
        [
            (ConfigError, BacktestError),
            (DataError, BacktestError),
            (MissingBarError, DataError),
            (OHLCInvalidError, DataError),
            (TimeOrderError, DataError),
            (IndicatorError, BacktestError),
            (IndicatorNaNError, IndicatorError),
            (IndicatorBufferError, IndicatorError),
            (ExecutionError, BacktestError),
            (InvalidPriceError, ExecutionError),
            (MarginCallError, ExecutionError),
        ],
    )
    def test_leaf_is_subclass_of_parent(self, leaf, parent):
        # Assert: isinstance 関係が成立する
        assert issubclass(leaf, parent)
        instance = leaf("x")
        assert isinstance(instance, parent)
        assert isinstance(instance, BacktestError)

    def test_data_error_leaves_are_not_execution_errors(self):
        # Assert: 系統間は混線しない
        assert not issubclass(OHLCInvalidError, ExecutionError)
        assert not issubclass(InvalidPriceError, DataError)

    def test_context_inherited_by_all_leaves(self):
        # Act
        err = OHLCInvalidError("bad", context={"high": 1.0})
        # Assert
        assert err.context == {"high": 1.0}
