"""MovingAverages の移動平均計算を検証するテスト。

元 MQL5 ``MovingAverages.mqh`` の挙動再現を、手計算した期待値および
スカラー版とバッファ版の整合性で確認する。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import (  # noqa: E402
    exponential_ma,
    exponential_ma_on_buffer,
    linear_weighted_ma,
    linear_weighted_ma_on_buffer,
    linear_weighted_ma_on_buffer_fast,
    simple_ma,
    simple_ma_on_buffer,
    smoothed_ma,
    smoothed_ma_on_buffer,
)

PRICE = np.array([1.0, 2.0, 3.0, 4.0, 5.0])


# ---------------------------------------------------------------------------
# スカラー版
# ---------------------------------------------------------------------------
def test_simple_ma_basic():
    # (5 + 4 + 3) / 3
    assert simple_ma(4, 3, PRICE) == pytest.approx(4.0)


def test_simple_ma_invalid_period_returns_zero():
    assert simple_ma(0, 3, PRICE) == 0.0  # period > position + 1
    assert simple_ma(4, 0, PRICE) == 0.0  # period <= 0


def test_exponential_ma_basic():
    # pr = 2/4 = 0.5; price[1]*0.5 + prev*0.5 = 2*0.5 + 1*0.5
    assert exponential_ma(1, 3, 1.0, PRICE) == pytest.approx(1.5)


def test_exponential_ma_invalid_period_returns_zero():
    assert exponential_ma(1, 0, 1.0, PRICE) == 0.0


def test_linear_weighted_ma_basic():
    # 重み 1,2,3 を価格 3,4,5 に: (3*1 + 4*2 + 5*3) / (1+2+3) = 26/6
    assert linear_weighted_ma(4, 3, PRICE) == pytest.approx(26.0 / 6.0)


def test_linear_weighted_ma_invalid_period_returns_zero():
    assert linear_weighted_ma(0, 3, PRICE) == 0.0


def test_smoothed_ma_seed_is_overwritten():
    # position == period-1 のシード単純平均は再帰式で上書きされる（元コードの癖）。
    # 結果は (prev_value*(period-1) + price[position]) / period に一致する。
    expected = (10.0 * 2 + PRICE[2]) / 3.0  # = 23/3
    assert smoothed_ma(2, 3, 10.0, PRICE) == pytest.approx(expected)


def test_smoothed_ma_invalid_period_returns_zero():
    assert smoothed_ma(1, 3, 10.0, PRICE) == 0.0  # period > position + 1


# ---------------------------------------------------------------------------
# バッファ版
# ---------------------------------------------------------------------------
def test_simple_ma_on_buffer_values_and_return():
    buffer = np.zeros_like(PRICE)
    ret = simple_ma_on_buffer(5, 0, 0, 3, PRICE, buffer)
    assert ret == 5
    # 先頭はゼロ埋め、シード以降は SMA
    np.testing.assert_allclose(buffer, [0.0, 0.0, 2.0, 3.0, 4.0])
    # 有効位置ではスカラー版と一致
    for pos in (2, 3, 4):
        assert buffer[pos] == pytest.approx(simple_ma(pos, 3, PRICE))


def test_simple_ma_on_buffer_invalid_period_returns_zero():
    buffer = np.zeros_like(PRICE)
    assert simple_ma_on_buffer(5, 0, 0, 1, PRICE, buffer) == 0  # period <= 1
    assert simple_ma_on_buffer(5, 0, 0, 6, PRICE, buffer) == 0  # period > total-begin


def test_exponential_ma_on_buffer_values():
    buffer = np.zeros_like(PRICE)
    ret = exponential_ma_on_buffer(5, 0, 0, 3, PRICE, buffer)
    assert ret == 5
    np.testing.assert_allclose(buffer, [1.0, 1.5, 2.25, 3.125, 4.0625])
    # メインループはスカラー EMA の漸化式に一致
    for pos in (3, 4):
        assert buffer[pos] == pytest.approx(
            exponential_ma(pos, 3, buffer[pos - 1], PRICE)
        )


def test_linear_weighted_ma_on_buffer_matches_scalar():
    buffer = np.zeros_like(PRICE)
    ret = linear_weighted_ma_on_buffer(5, 0, 0, 3, PRICE, buffer)
    assert ret == 5
    for pos in (2, 3, 4):
        assert buffer[pos] == pytest.approx(linear_weighted_ma(pos, 3, PRICE))


def test_linear_weighted_ma_on_buffer_fast_matches_classic():
    buf_classic = np.zeros_like(PRICE)
    buf_fast = np.zeros_like(PRICE)
    linear_weighted_ma_on_buffer(5, 0, 0, 3, PRICE, buf_classic)
    ret, weight_sum = linear_weighted_ma_on_buffer_fast(5, 0, 0, 3, PRICE, buf_fast)
    assert ret == 5
    assert weight_sum == 6  # 1 + 2 + 3
    np.testing.assert_allclose(buf_fast, buf_classic)


def test_smoothed_ma_on_buffer_values():
    buffer = np.zeros_like(PRICE)
    ret = smoothed_ma_on_buffer(5, 0, 0, 3, PRICE, buffer)
    assert ret == 5
    # シード = SMA、以降 (prev*(period-1)+price)/period
    np.testing.assert_allclose(buffer, [0.0, 0.0, 2.0, 8.0 / 3.0, 31.0 / 9.0])


def test_on_buffer_with_begin_offset():
    # begin より前はゼロ埋めされ、計算は begin から始まる
    buffer = np.zeros_like(PRICE)
    ret = simple_ma_on_buffer(5, 0, 1, 3, PRICE, buffer)
    assert ret == 5
    assert buffer[0] == 0.0
    # start_position = period + begin = 4 → buffer[3] が最初のシード値
    assert buffer[3] == pytest.approx((PRICE[1] + PRICE[2] + PRICE[3]) / 3.0)
