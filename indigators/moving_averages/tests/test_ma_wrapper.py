"""狭いラッパ ``ma(price, ma_type, length)`` の検証（ISSUE-182 項目 2）。

既存の 6 引数 out-param 版（``*_ma_on_buffer``）は MQL 1:1 資産として残置し、
本ラッパはその上に載る「純粋関数」面である。したがって検証の中心は
**既存 6 引数版と bit-for-bit 同一の出力を返すこと**（``tobytes()`` 比較）に置く。

種別キー（sma / ema / smma / lwma）は既存の種別写像
``moving_averages/src/lwc_chart.py`` の ``_MA_FUNCS`` と同一集合であることを
突合して固定する（独自体系を作らない）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import (  # noqa: E402
    exponential_ma_on_buffer,
    linear_weighted_ma_on_buffer,
    ma,
    simple_ma_on_buffer,
    smoothed_ma_on_buffer,
)

# 既存 6 引数版（参照実装）。ma_type キー → 関数。
_REFERENCE = {
    "sma": simple_ma_on_buffer,
    "ema": exponential_ma_on_buffer,
    "smma": smoothed_ma_on_buffer,
    "lwma": linear_weighted_ma_on_buffer,
}

_TYPES = ("sma", "ema", "smma", "lwma")


def _reference_output(price: np.ndarray, ma_type: str, length: int) -> np.ndarray:
    """既存 6 引数版を「現行の本番呼出と同一の作法」で呼んだ結果を返す。

    本番 17 本すべてが ``prev_calculated=0`` / ``begin=0`` / 事前 ``np.zeros(n)`` 確保。
    """
    n = int(price.shape[0])
    buffer = np.zeros(n, dtype=np.float64)
    _REFERENCE[ma_type](n, 0, 0, length, price, buffer)
    return buffer


@pytest.fixture(scope="module")
def real_price() -> np.ndarray:
    """実データ（jp225_m1.csv）の close 列先頭 3000 本。無い場合は skip。"""
    csv = Path(__file__).resolve().parents[3] / "data" / "marketdata" / "jp225_m1.csv"
    if not csv.exists():
        pytest.skip(f"実データが存在しません: {csv}")
    values: list[float] = []
    with csv.open() as fh:
        fh.readline()  # header
        for _ in range(3000):
            line = fh.readline()
            if not line:
                break
            values.append(float(line.split(",")[4]))
    return np.asarray(values, dtype=np.float64)


# =========================================================== 種別写像の整合
def test_ma_type_keys_match_existing_lwc_chart_mapping():
    """ラッパの受理種別が既存 ``lwc_chart._MA_FUNCS`` のキー集合と一致する。"""
    from src.lwc_chart import _MA_FUNCS  # noqa: PLC0415

    for key, fn in _MA_FUNCS.items():
        assert key in _TYPES
        assert _REFERENCE[key] is fn
    assert set(_MA_FUNCS) == set(_TYPES)


# =========================================================== bit-for-bit 一致
@pytest.mark.parametrize("ma_type", _TYPES)
@pytest.mark.parametrize("length", [2, 3, 5, 9, 21, 200])
def test_ma_matches_on_buffer_bit_for_bit_on_real_data(real_price, ma_type, length):
    # Arrange
    expected = _reference_output(real_price, ma_type, length)
    # Act
    got = ma(real_price, ma_type, length)
    # Assert（bit-for-bit）
    assert got.tobytes() == expected.tobytes()
    assert got.dtype == expected.dtype
    assert got.shape == expected.shape


@pytest.mark.parametrize("ma_type", _TYPES)
def test_ma_matches_on_buffer_bit_for_bit_on_synthetic_series(ma_type):
    price = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0, 10.0])
    expected = _reference_output(price, ma_type, 3)
    assert ma(price, ma_type, 3).tobytes() == expected.tobytes()


# =========================================================== 境界・異常系
@pytest.mark.parametrize("ma_type", _TYPES)
@pytest.mark.parametrize("length", [0, 1, 6])
def test_ma_invalid_period_returns_zeros_like_existing_contract(ma_type, length):
    """``period<=1`` / ``period>n`` は既存契約どおり buffer 未更新（全 0）。"""
    price = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    expected = _reference_output(price, ma_type, length)
    got = ma(price, ma_type, length)
    assert got.tobytes() == expected.tobytes()
    assert np.all(got == 0.0)


def test_ma_unknown_type_raises_value_error():
    price = np.array([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        ma(price, "kama", 2)


def test_ma_type_is_case_insensitive():
    price = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert ma(price, "EMA", 3).tobytes() == ma(price, "ema", 3).tobytes()


def test_ma_does_not_mutate_input_price():
    price = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    snapshot = price.tobytes()
    ma(price, "ema", 3)
    assert price.tobytes() == snapshot


def test_ma_accepts_non_float64_input_and_returns_float64():
    price = np.array([1, 2, 3, 4, 5], dtype=np.int64)
    got = ma(price, "sma", 3)
    assert got.dtype == np.float64
    assert got.shape == (5,)


def test_ma_empty_series_returns_empty_float64_array():
    got = ma(np.array([], dtype=np.float64), "ema", 3)
    assert got.shape == (0,)
    assert got.dtype == np.float64
