"""適用価格（applied price）7 種の計算を検証するテスト。

各式を手計算した期待値で固定し、ディスパッチャの切り替えと異常系を確認する。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# 共有層をパッケージとして import 可能にする（リポジトリルートを追加）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common import (  # noqa: E402
    AppliedPrice,
    applied_price,
    close_price,
    high_price,
    low_price,
    median_price,
    ohlc4_price,
    open_price,
    typical_price,
    weighted_price,
)

OPEN = np.array([5.0, 12.0])
HIGH = np.array([10.0, 20.0])
LOW = np.array([2.0, 4.0])
CLOSE = np.array([8.0, 16.0])


# ---------------------------------------------------------------------------
# 単純な列選択
# ---------------------------------------------------------------------------
def test_simple_selections():
    np.testing.assert_allclose(close_price(CLOSE), CLOSE)
    np.testing.assert_allclose(open_price(OPEN), OPEN)
    np.testing.assert_allclose(high_price(HIGH), HIGH)
    np.testing.assert_allclose(low_price(LOW), LOW)


def test_selection_returns_float_array():
    result = close_price(np.array([1, 2, 3]))  # 整数入力
    assert result.dtype == float


# ---------------------------------------------------------------------------
# 算術合成
# ---------------------------------------------------------------------------
def test_median_price():
    # (10+2)/2=6, (20+4)/2=12
    np.testing.assert_allclose(median_price(HIGH, LOW), [6.0, 12.0])


def test_typical_price():
    # (10+2+8)/3=20/3, (20+4+16)/3=40/3
    np.testing.assert_allclose(
        typical_price(HIGH, LOW, CLOSE), [20.0 / 3.0, 40.0 / 3.0]
    )


def test_weighted_price():
    # (10+2+2*8)/4=7, (20+4+2*16)/4=14
    np.testing.assert_allclose(weighted_price(HIGH, LOW, CLOSE), [7.0, 14.0])


def test_ohlc4_price():
    # (5+10+2+8)/4=6.25, (12+20+4+16)/4=13（MQL 外拡張）
    np.testing.assert_allclose(ohlc4_price(OPEN, HIGH, LOW, CLOSE), [6.25, 13.0])


# ---------------------------------------------------------------------------
# ディスパッチャ
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind, expected",
    [
        (AppliedPrice.CLOSE, CLOSE),
        (AppliedPrice.OPEN, OPEN),
        (AppliedPrice.HIGH, HIGH),
        (AppliedPrice.LOW, LOW),
        (AppliedPrice.MEDIAN, [6.0, 12.0]),
        (AppliedPrice.TYPICAL, [20.0 / 3.0, 40.0 / 3.0]),
        (AppliedPrice.WEIGHTED, [7.0, 14.0]),
        (AppliedPrice.OHLC4, [6.25, 13.0]),
    ],
)
def test_applied_price_dispatch(kind, expected):
    np.testing.assert_allclose(
        applied_price(kind, OPEN, HIGH, LOW, CLOSE), expected
    )


def test_applied_price_accepts_int_value():
    # AppliedPrice と同値の int でも切り替わる（MQL ENUM 互換）
    np.testing.assert_allclose(
        applied_price(6, OPEN, HIGH, LOW, CLOSE), [20.0 / 3.0, 40.0 / 3.0]
    )


def test_applied_price_enum_values_match_mql():
    # MQL ENUM_APPLIED_PRICE の数値と一致
    assert int(AppliedPrice.CLOSE) == 1
    assert int(AppliedPrice.OPEN) == 2
    assert int(AppliedPrice.HIGH) == 3
    assert int(AppliedPrice.LOW) == 4
    assert int(AppliedPrice.MEDIAN) == 5
    assert int(AppliedPrice.TYPICAL) == 6
    assert int(AppliedPrice.WEIGHTED) == 7
    # OHLC4 は MQL 外拡張（値 8）。
    assert int(AppliedPrice.OHLC4) == 8


def test_applied_price_unknown_kind_raises():
    with pytest.raises(ValueError):
        applied_price(99, OPEN, HIGH, LOW, CLOSE)
