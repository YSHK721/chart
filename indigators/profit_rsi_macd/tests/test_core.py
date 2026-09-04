"""PRO!fitRSIMACD core 層（純粋計算）の検証。

元 MQL4 ``PRO!fitRSIMACD.mq4`` を昇順（古→新, index 0=最古）へ 1:1 変換した
一意定義を固定する。本指標は profit_mfi_macd（MACD 連鎖 ＋ σ7 水準）と同型で、
iMFI を iRSI（権威 Wilder）に置換し、価格を PRICE_TYPICAL 固定にしたもの。

確定セマンティクス（昇順・1:1 再現・元コードの計算順序）::

    1. price = typical_price(high, low, close) = (high + low + close) / 3
       rsi   = compute_rsi(price, period=RSIperiod=13)  # 権威 Wilder（複製）
    2. fast = EMA(rsi, FastEMA=4) ; slow = EMA(rsi, SlowEMA=8)
    3. macd[i] = fast[i] - slow[i]
    4. signal = EMA(macd, SignalEMA=4)
    5. histogram[i] = 2.618 * (macd[i] - signal[i])
    6. σ7水準は histogram（=2.618 適用後）全系列。母σ÷N。mid50=50。

iRSI（複製 compute_rsi）の場合分け（profit_rsi と byte-identical を志向）::

    warm-up (i < period)                 -> 0.0
    seed (i == period): pos/neg = period 本の up/down 平均
    main (i > period)  : pos[i]=(pos[i-1]*(period-1)+up)/period（Wilder 平滑）
    RSI[i]: neg!=0 -> 100-100/(1+pos/neg); neg==0&pos!=0 -> 100; flat -> 50
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    DEFAULT_FAST_EMA,
    DEFAULT_RSI_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    RsiMacdResult,
    compute_rsi,
    compute_rsimacd,
    compute_rsimacd_levels,
)

# 共有 EMA 関数（照合用）。
from moving_averages import exponential_ma_on_buffer  # noqa: E402

# 共有 common（適用価格・照合用）。
from common import typical_price  # noqa: E402


# ---------------------------------------------------------------------------
# テスト独立の参照実装（元 iRSI を昇順で素朴再実装 — 複製ロジックの照合用）
# ---------------------------------------------------------------------------
def _reference_rsi(price, period):
    price = np.asarray(price, dtype=float)
    n = price.shape[0]
    out = np.zeros(n)
    if n <= period:
        return out
    sp = sn = 0.0
    for i in range(1, period + 1):
        d = price[i] - price[i - 1]
        sp += d if d > 0 else 0.0
        sn += -d if d < 0 else 0.0
    pos = sp / period
    neg = sn / period

    def r(p, ng):
        if ng != 0.0:
            return 100.0 - 100.0 / (1.0 + p / ng)
        if p != 0.0:
            return 100.0
        return 50.0

    out[period] = r(pos, neg)
    for i in range(period + 1, n):
        d = price[i] - price[i - 1]
        pos = (pos * (period - 1) + (d if d > 0 else 0.0)) / period
        neg = (neg * (period - 1) + (-d if d < 0 else 0.0)) / period
        out[i] = r(pos, neg)
    return out


def _make_ohlc(n=20):
    """単調増加の合成 OHLC（昇順）。high - low = 2, high - close = 1。"""
    high = np.arange(10.0, 10.0 + n, dtype=float)
    low = high - 2.0
    close = high - 1.0
    open_ = high - 1.5
    return open_, high, low, close


def _make_asym_ohlc(n=20):
    """非対称 OHLC。typical=(h+l+c)/3 が close/high/median のいずれとも異なる。

    ``_make_ohlc`` は close=(h+l+c)/3 となり typical==close で縮退するため、
    価格=Typical を判別する検証には本フィクスチャを用いる（typical≠close）。
    """
    idx = np.arange(n)
    high = 10.0 + idx + (idx % 3) * 0.7
    low = high - (1.0 + (idx % 4) * 0.5)
    close = low + 0.3 + (idx % 2) * 0.4
    open_ = low + 0.1
    return open_, high, low, close


# ===========================================================================
# 1. デフォルト定数
# ===========================================================================
def test_default_constants_match_extern_RSI13_Fast4_Slow8_Signal4():
    # Arrange / Act / Assert
    assert DEFAULT_RSI_PERIOD == 13
    assert DEFAULT_FAST_EMA == 4
    assert DEFAULT_SLOW_EMA == 8
    assert DEFAULT_SIGNAL_EMA == 4


# ===========================================================================
# 2. iRSI 一致（権威 Wilder・複製の 1:1 再現）
# ===========================================================================
def test_compute_rsi_matches_authoritative_wilder_reference_on_typical():
    # Arrange
    _, high, low, close = _make_ohlc(20)
    price = (high + low + close) / 3.0
    # Act
    actual = compute_rsi(price, period=13)
    # Assert: 独立参照（権威 Wilder）と全系列一致
    np.testing.assert_allclose(actual, _reference_rsi(price, 13), rtol=0, atol=1e-12)


def test_compute_rsi_warmup_region_is_zero_not_nan():
    # Arrange
    price = np.arange(1.0, 21.0)
    # Act
    rsi = compute_rsi(price, period=13)
    # Assert: warm-up（i < period=13）は 0.0（NaN ではない）
    assert np.all(rsi[:13] == 0.0)
    assert not np.any(np.isnan(rsi))


def test_compute_rsi_flat_window_returns_50():
    # Arrange: 完全 flat（neg==0 かつ pos==0）
    price = [10.0, 10.0, 10.0, 10.0, 10.0]
    # Act
    rsi = compute_rsi(price, period=2)
    # Assert: flat -> 50（warm-up 0 を除く）。手計算: [0, 0, 50, 50, 50]
    np.testing.assert_array_equal(rsi, [0.0, 0.0, 50.0, 50.0, 50.0])


def test_compute_rsi_all_up_window_returns_100():
    # Arrange: 単調増加（neg==0 かつ pos!=0）
    price = [1.0, 2.0, 3.0, 4.0, 5.0]
    # Act
    rsi = compute_rsi(price, period=2)
    # Assert: neg==0 & pos!=0 -> 100。手計算: [0, 0, 100, 100, 100]
    np.testing.assert_array_equal(rsi, [0.0, 0.0, 100.0, 100.0, 100.0])


def test_compute_rsi_seed_uses_simple_mean_over_period_bars():
    # Arrange: 既知の差分列で seed の sum/period を固定
    price = [10.0, 12.0, 11.0, 13.0]  # diff = +2, -1, +2
    # Act: period=3 -> seed i==3: pos=(2+0+2)/3, neg=(0+1+0)/3
    rsi = compute_rsi(price, period=3)
    # Assert
    pos = (2.0 + 0.0 + 2.0) / 3.0
    neg = (0.0 + 1.0 + 0.0) / 3.0
    expected = 100.0 - 100.0 / (1.0 + pos / neg)
    assert rsi[3] == pytest.approx(expected, abs=1e-12)


def test_compute_rsi_raises_value_error_when_period_below_2():
    with pytest.raises(ValueError):
        compute_rsi(np.arange(10.0), period=1)


# ===========================================================================
# 3. 価格 = Typical 固定
# ===========================================================================
def test_compute_rsimacd_uses_typical_price_high_low_close_over_3():
    # Arrange: 非対称 OHLC（typical≠close≠high）で価格選択を判別可能にする
    open_, high, low, close = _make_asym_ohlc(20)
    # Act: rsi は typical price 由来であるべき
    result = compute_rsimacd(open_, high, low, close)
    expected_price = (high + low + close) / 3.0
    expected_rsi = compute_rsi(expected_price, period=13)
    # Assert: open は無視され、price=(h+l+c)/3 で算出される
    np.testing.assert_allclose(result.rsi, expected_rsi, rtol=0, atol=1e-12)


def test_compute_rsimacd_price_is_typical_not_close_discriminating():
    # Arrange: typical と close が異なる非対称 OHLC
    open_, high, low, close = _make_asym_ohlc(20)
    assert not np.allclose((high + low + close) / 3.0, close)  # フィクスチャ前提
    # Act
    result = compute_rsimacd(open_, high, low, close)
    rsi_from_close = compute_rsi(close, period=13)
    # Assert: rsi は close 由来ではない（price=CLOSE 実装なら fail する discriminating）
    assert not np.allclose(result.rsi, rsi_from_close)


def test_compute_rsimacd_price_is_typical_not_high_discriminating():
    # Arrange
    open_, high, low, close = _make_asym_ohlc(20)
    assert not np.allclose((high + low + close) / 3.0, high)  # フィクスチャ前提
    # Act
    result = compute_rsimacd(open_, high, low, close)
    rsi_from_high = compute_rsi(high, period=13)
    # Assert: rsi は high 由来でもない
    assert not np.allclose(result.rsi, rsi_from_high)


def test_typical_price_helper_is_high_low_close_over_3():
    # Arrange
    _, high, low, close = _make_ohlc(5)
    # Act
    typ = typical_price(high, low, close)
    # Assert: 手計算 [9, 10, 11, 12, 13]
    np.testing.assert_array_equal(typ, [9.0, 10.0, 11.0, 12.0, 13.0])


# ===========================================================================
# 4. EMA 連鎖（共有 exponential_ma_on_buffer と一致）
# ===========================================================================
def test_compute_rsimacd_fast_slow_match_shared_ema_on_buffer():
    # Arrange
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    rsi = result.rsi
    n = rsi.shape[0]
    # Act: 共有 EMA で fast/slow を独立再現
    fast_ref = np.zeros(n)
    exponential_ma_on_buffer(n, 0, 0, 4, rsi, fast_ref)
    slow_ref = np.zeros(n)
    exponential_ma_on_buffer(n, 0, 0, 8, rsi, slow_ref)
    # Assert
    np.testing.assert_allclose(result.fast, fast_ref, rtol=0, atol=1e-12)
    np.testing.assert_allclose(result.slow, slow_ref, rtol=0, atol=1e-12)


def test_compute_rsimacd_macd_is_fast_minus_slow():
    # Arrange
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    # Act / Assert
    np.testing.assert_allclose(
        result.macd, result.fast - result.slow, rtol=0, atol=1e-12
    )


def test_compute_rsimacd_signal_is_ema_of_macd_period_4():
    # Arrange
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    n = result.macd.shape[0]
    # Act: 共有 EMA で signal を独立再現
    signal_ref = np.zeros(n)
    exponential_ma_on_buffer(n, 0, 0, 4, result.macd, signal_ref)
    # Assert
    np.testing.assert_allclose(result.signal, signal_ref, rtol=0, atol=1e-12)


# ===========================================================================
# 5. histogram = 2.618 * (macd - signal)（係数厳密）
# ===========================================================================
def test_compute_rsimacd_histogram_is_2618_times_macd_minus_signal():
    # Arrange
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    # Act / Assert: 係数 2.618 厳密（係数違いで fail する discriminating）
    np.testing.assert_allclose(
        result.histogram,
        2.618 * (result.macd - result.signal),
        rtol=0,
        atol=1e-12,
    )


def test_compute_rsimacd_histogram_last_value_handcalc_discriminates_coefficient():
    # Arrange: 単調増加 20 本（手計算済み固定値）
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    # Act / Assert: 手計算 hist[-1] = 2.618*(macd[-1]-signal[-1]) = -7.791038988397577
    assert result.histogram[-1] == pytest.approx(-7.791038988397577, abs=1e-9)


# ===========================================================================
# 6. σ7水準（histogram=係数適用後・母σ÷N・mid50=50）
# ===========================================================================
def test_compute_rsimacd_levels_uses_population_sigma_on_histogram():
    # Arrange: histogram 全系列（係数適用後）
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    hist = result.histogram
    avg = float(np.mean(hist))
    sigma = float(np.sqrt(np.mean((hist - avg) ** 2)))  # 母σ÷N
    # Act
    levels = compute_rsimacd_levels(hist)
    # Assert
    assert levels["p1"] == pytest.approx(avg + sigma, abs=1e-12)
    assert levels["p2"] == pytest.approx(avg + 2 * sigma, abs=1e-12)
    assert levels["p3"] == pytest.approx(avg + 3 * sigma, abs=1e-12)
    assert levels["m1"] == pytest.approx(avg - sigma, abs=1e-12)
    assert levels["m2"] == pytest.approx(avg - 2 * sigma, abs=1e-12)
    assert levels["m3"] == pytest.approx(avg - 3 * sigma, abs=1e-12)
    assert levels["mid50"] == 50.0


def test_compute_rsimacd_levels_population_sigma_discriminates_from_sample_sigma():
    # Arrange
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    hist = result.histogram
    avg = float(np.mean(hist))
    pop_sigma = float(np.sqrt(np.mean((hist - avg) ** 2)))  # ÷N
    sample_sigma = float(np.std(hist, ddof=1))  # ÷(N-1)
    levels = compute_rsimacd_levels(hist)
    # Assert: 母σ÷N（手計算 9.379996...）であり、標本σ（9.623672...）ではない
    assert pop_sigma != pytest.approx(sample_sigma, abs=1e-6)
    assert levels["p1"] - avg == pytest.approx(pop_sigma, abs=1e-9)
    assert levels["p1"] - avg != pytest.approx(sample_sigma, abs=1e-6)


def test_compute_rsimacd_levels_sigma_applied_after_2618_coefficient():
    # Arrange: 係数適用前 (macd-signal) と適用後 (histogram) で σ が異なることを固定
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    pre = result.macd - result.signal  # 係数適用前
    post = result.histogram            # 係数適用後 (= 2.618 * pre)
    sigma_pre = float(np.sqrt(np.mean((pre - np.mean(pre)) ** 2)))
    sigma_post = float(np.sqrt(np.mean((post - np.mean(post)) ** 2)))
    levels = compute_rsimacd_levels(post)
    avg_post = float(np.mean(post))
    # Assert: levels は post（係数適用後）の σ に基づく（pre ではない）
    assert sigma_post == pytest.approx(2.618 * sigma_pre, abs=1e-9)
    assert levels["p1"] - avg_post == pytest.approx(sigma_post, abs=1e-9)
    assert levels["p1"] - avg_post != pytest.approx(sigma_pre, abs=1e-6)


def test_compute_rsimacd_result_levels_has_seven_keys():
    # Arrange / Act
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    # Assert
    assert set(result.levels.keys()) == {
        "p1", "p2", "p3", "m1", "m2", "m3", "mid50",
    }


# ===========================================================================
# 7. 例外
# ===========================================================================
def test_compute_rsimacd_raises_value_error_when_rsi_period_below_2():
    open_, high, low, close = _make_ohlc(20)
    with pytest.raises(ValueError):
        compute_rsimacd(open_, high, low, close, rsi_period=1)


def test_compute_rsimacd_raises_value_error_on_length_mismatch():
    open_, high, low, close = _make_ohlc(20)
    with pytest.raises(ValueError):
        compute_rsimacd(open_, high, low, close[:-1])


# ===========================================================================
# 8. DTO 不変性（frozen ＋ writeable=False）
# ===========================================================================
def test_rsimacd_result_arrays_are_read_only():
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    for name in ("rsi", "fast", "slow", "macd", "signal", "histogram"):
        arr = getattr(result, name)
        assert arr.flags.writeable is False, name
        with pytest.raises(ValueError):
            arr[0] = 123.456


def test_rsimacd_result_is_frozen_dataclass():
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    with pytest.raises(Exception):
        result.rsi = np.zeros(5)  # frozen -> FrozenInstanceError


def test_rsimacd_result_exposes_all_intermediate_series():
    open_, high, low, close = _make_ohlc(20)
    result = compute_rsimacd(open_, high, low, close)
    assert isinstance(result, RsiMacdResult)
    for name in ("rsi", "fast", "slow", "macd", "signal", "histogram"):
        assert isinstance(getattr(result, name), np.ndarray)
