"""PRO!fitRSI core 層（純粋計算）の検証。

元 MQL4 ``PRO!fitRSI.mq4`` が呼ぶ標準 ``iRSI``（Wilder RSI）＋ EMA 平滑 ＋ σ 水準を
昇順（古→新, index 0=最古）へ 1:1 変換した一意定義を固定する。

確定セマンティクス（MetaQuotes 公式 ``RSI.mq5`` の iRSI 実装に厳密準拠）::

    diff[i] = price[i] - price[i-1]
    warm-up (i < period)                 -> 0.0（元 iRSI/SetIndexDrawBegin 既定）
    seed (i == period):
        pos = mean_{j=1..period}(max(diff[j], 0))
        neg = mean_{j=1..period}(max(-diff[j], 0))
    main (i > period)  Wilder 平滑:
        pos[i] = (pos[i-1]*(period-1) + max(diff[i], 0)) / period
        neg[i] = (neg[i-1]*(period-1) + max(-diff[i], 0)) / period
    RSI[i]:
        neg != 0            -> 100 - 100/(1 + pos/neg)
        neg == 0, pos != 0  -> 100
        neg == 0, pos == 0  -> 50

compute_rsi_full は OHLC ＋ apply を入口に取り、Apply→適用価格 写像で共有
common.applied_price（7 種）を選択して RSI/EMA/σ 水準を統合する。
σ 水準は **生 RSI 系列**（ma ではない）全体（warm-up 0 込み）の平均と母標準偏差
（÷N）で算出する。EMA 平滑は共有 moving_averages.exponential_ma_on_buffer を再利用する。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    APPLY_TO_PRICE,
    DEFAULT_APPLY,
    DEFAULT_MA_PERIOD,
    DEFAULT_RSI_PERIOD,
    RsiResult,
    compute_rsi,
    compute_rsi_full,
    compute_rsi_levels,
)

# 共有 EMA 関数（照合用）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from moving_averages import exponential_ma_on_buffer  # noqa: E402

# 共有 common（適用価格・照合用）。
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from common import AppliedPrice, applied_price  # noqa: E402


def _reference_rsi(price, period):
    """テスト独立の参照 RSI（元 iRSI を昇順で素朴再実装）。"""
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
    if neg != 0:
        out[period] = 100.0 - 100.0 / (1.0 + pos / neg)
    else:
        out[period] = 100.0 if pos != 0 else 50.0
    for i in range(period + 1, n):
        d = price[i] - price[i - 1]
        pos = (pos * (period - 1) + (d if d > 0 else 0.0)) / period
        neg = (neg * (period - 1) + (-d if d < 0 else 0.0)) / period
        if neg != 0:
            out[i] = 100.0 - 100.0 / (1.0 + pos / neg)
        else:
            out[i] = 100.0 if pos != 0 else 50.0
    return out


def _ohlc8():
    """8 本の非対称 OHLC（各列が判別可能に異なる値を持つ）。"""
    open_ = np.array([10.0, 11.0, 10.5, 12.0, 11.0, 11.0, 12.5, 13.0])
    high = np.array([10.8, 11.7, 11.2, 12.6, 11.4, 11.9, 13.2, 13.6])
    low = np.array([9.3, 10.2, 10.1, 11.4, 10.6, 10.4, 12.1, 12.7])
    close = np.array([10.2, 11.3, 10.4, 12.2, 11.1, 11.6, 12.8, 13.1])
    return open_, high, low, close


# ---------------------------------------------------------------------------
# TC-01 iRSI 手計算固定（period=3, N=6・正負分類）
# ---------------------------------------------------------------------------
def test_compute_rsi_returns_hand_calculated_values_for_period3_n6():
    # Arrange: price diffs = [+1, -0.5, +1.5, -1, 0]
    price = np.array([10.0, 11.0, 10.5, 12.0, 11.0, 11.0])
    # seed i=3: pos=mean(1,0,1.5)=2.5/3, neg=mean(0,0.5,0)=0.5/3 -> RS=5 -> 100-100/6
    expected = _reference_rsi(price, 3)

    # Act
    result = compute_rsi(price, period=3)

    # Assert
    np.testing.assert_allclose(result, expected, rtol=1e-12)
    assert result[3] == pytest.approx(100.0 - 100.0 / 6.0)


# ---------------------------------------------------------------------------
# TC-02 warm-up 0（i < period は 0）
# ---------------------------------------------------------------------------
def test_compute_rsi_returns_zero_for_warmup_bars_below_period():
    # Arrange
    price = np.array([10.0, 11.0, 12.0, 13.0])

    # Act
    result = compute_rsi(price, period=3)

    # Assert: i=0,1,2 は warm-up で 0（NaN ではない）。
    assert result[0] == 0.0
    assert result[1] == 0.0
    assert result[2] == 0.0


# ---------------------------------------------------------------------------
# TC-03 all-up（neg==0, pos!=0）-> seed で 100
# ---------------------------------------------------------------------------
def test_compute_rsi_returns_100_when_all_diffs_positive():
    # Arrange: 単調増加 -> neg=0, pos!=0
    price = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

    # Act
    result = compute_rsi(price, period=3)

    # Assert
    assert result[3] == 100.0
    assert result[4] == 100.0


# ---------------------------------------------------------------------------
# TC-04 flat（neg==0 かつ pos==0）-> seed で 50（MFI の 100 と異なる）
# ---------------------------------------------------------------------------
def test_compute_rsi_returns_50_when_window_is_flat():
    # Arrange: 全て同値 -> pos=neg=0
    price = np.array([5.0, 5.0, 5.0, 5.0, 5.0])

    # Act
    result = compute_rsi(price, period=3)

    # Assert: flat は 50（100 ではない）。
    assert result[3] == 50.0
    assert result[4] == 50.0
    assert result[3] != 100.0


# ---------------------------------------------------------------------------
# TC-05 period < 2 は ValueError
# ---------------------------------------------------------------------------
def test_compute_rsi_raises_for_period_below_2():
    price = np.array([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        compute_rsi(price, period=1)


# ---------------------------------------------------------------------------
# TC-06 rates_total <= period は全 0（元 RSI.mq5 の rates_total<=period return 0）
# ---------------------------------------------------------------------------
def test_compute_rsi_returns_all_zero_when_length_not_exceeding_period():
    price = np.array([1.0, 2.0, 3.0])  # n=3 == period
    result = compute_rsi(price, period=3)
    np.testing.assert_array_equal(result, np.zeros(3))


# ---------------------------------------------------------------------------
# TC-07 Apply→適用価格 写像（1=OPEN/2=HIGH/3=LOW/4=MEDIAN/5=TYPICAL/6=WEIGHTED）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "apply, expected_kind",
    [
        (1, AppliedPrice.OPEN),
        (2, AppliedPrice.HIGH),
        (3, AppliedPrice.LOW),
        (4, AppliedPrice.MEDIAN),
        (5, AppliedPrice.TYPICAL),
        (6, AppliedPrice.WEIGHTED),
        (0, AppliedPrice.CLOSE),    # それ以外 -> CLOSE
        (7, AppliedPrice.CLOSE),    # それ以外 -> CLOSE
        (99, AppliedPrice.CLOSE),   # それ以外 -> CLOSE
    ],
)
def test_apply_to_price_map_dispatches_to_common_applied_price(apply, expected_kind):
    # Arrange / Assert: 写像表が common.AppliedPrice へ正しく対応する。
    assert APPLY_TO_PRICE(apply) == expected_kind


# ---------------------------------------------------------------------------
# TC-08 各 Apply で compute_rsi_full が正しい価格系列の RSI を出す
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "apply, kind",
    [
        (1, AppliedPrice.OPEN),
        (2, AppliedPrice.HIGH),
        (3, AppliedPrice.LOW),
        (4, AppliedPrice.MEDIAN),
        (5, AppliedPrice.TYPICAL),
        (6, AppliedPrice.WEIGHTED),
        (0, AppliedPrice.CLOSE),
    ],
)
def test_compute_rsi_full_uses_applied_price_selected_by_apply(apply, kind):
    # Arrange
    open_, high, low, close = _ohlc8()
    price = applied_price(kind, open_, high, low, close)
    expected_rsi = _reference_rsi(price, 3)

    # Act
    result = compute_rsi_full(
        open_, high, low, close, rsi_period=3, apply=apply, ma_period=2
    )

    # Assert
    np.testing.assert_allclose(result.rsi, expected_rsi, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-09 既定 apply（=5 -> TYPICAL）が使われる
# ---------------------------------------------------------------------------
def test_compute_rsi_full_default_apply_is_typical():
    # Arrange
    open_, high, low, close = _ohlc8()
    typical = applied_price(AppliedPrice.TYPICAL, open_, high, low, close)
    expected = _reference_rsi(typical, 3)

    # Act（apply 省略 = DEFAULT_APPLY=5）。
    result = compute_rsi_full(open_, high, low, close, rsi_period=3, ma_period=2)

    # Assert
    assert DEFAULT_APPLY == 5
    np.testing.assert_allclose(result.rsi, expected, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-10 σ 水準（avg ± 1/2/3σ・母σ÷N ＋ mid50=50）— 標本σ÷(N-1) では fail する
# ---------------------------------------------------------------------------
def test_compute_rsi_levels_uses_population_sigma_and_mid50():
    # Arrange
    series = np.array([0.0, 0.0, 0.0, 80.0, 60.0, 40.0])
    avg = float(np.mean(series))
    pop_sigma = float(np.sqrt(np.mean((series - avg) ** 2)))  # ÷N（母σ）
    sample_sigma = float(np.std(series, ddof=1))  # ÷(N-1)（標本σ）

    # Act
    levels = compute_rsi_levels(series)

    # Assert: 母σ（÷N）であり、標本σ（÷N-1）ではない。
    assert levels["p1"] == pytest.approx(avg + pop_sigma)
    assert levels["p1"] != pytest.approx(avg + sample_sigma)
    assert levels["p2"] == pytest.approx(avg + 2 * pop_sigma)
    assert levels["p3"] == pytest.approx(avg + 3 * pop_sigma)
    assert levels["m1"] == pytest.approx(avg - pop_sigma)
    assert levels["m2"] == pytest.approx(avg - 2 * pop_sigma)
    assert levels["m3"] == pytest.approx(avg - 3 * pop_sigma)
    assert levels["mid50"] == 50.0


# ---------------------------------------------------------------------------
# TC-11 σ は生 RSI 系列に掛かる（ma ではない！）
# ---------------------------------------------------------------------------
def test_compute_rsi_full_levels_are_computed_on_raw_rsi_not_ma():
    # Arrange: rsi と ma が十分に異なる系列を使う。
    open_, high, low, close = _ohlc8()
    result = compute_rsi_full(
        open_, high, low, close, rsi_period=3, apply=0, ma_period=2
    )
    levels_from_rsi = compute_rsi_levels(result.rsi)
    levels_from_ma = compute_rsi_levels(result.ma)

    # Assert: levels は生 RSI 由来であり、ma 由来ではない。
    assert result.levels == pytest.approx(levels_from_rsi)
    assert result.levels != pytest.approx(levels_from_ma)


# ---------------------------------------------------------------------------
# TC-12 compute_rsi_full は RsiResult（rsi / ma / levels）を返し EMA は共有一致
# ---------------------------------------------------------------------------
def test_compute_rsi_full_returns_result_with_shared_ema_and_levels():
    # Arrange
    open_, high, low, close = _ohlc8()
    price = applied_price(AppliedPrice.CLOSE, open_, high, low, close)
    rsi = _reference_rsi(price, 3)
    ma_ref = np.zeros(rsi.shape[0])
    exponential_ma_on_buffer(rsi.shape[0], 0, 0, 2, rsi, ma_ref)
    levels_ref = compute_rsi_levels(rsi)  # 生 RSI 由来

    # Act
    result = compute_rsi_full(
        open_, high, low, close, rsi_period=3, apply=0, ma_period=2
    )

    # Assert
    assert isinstance(result, RsiResult)
    np.testing.assert_allclose(result.rsi, rsi, rtol=1e-12)
    np.testing.assert_allclose(result.ma, ma_ref, rtol=1e-12)
    assert result.levels == pytest.approx(levels_ref)


# ---------------------------------------------------------------------------
# TC-13 OHLC 長不一致は ValueError
# ---------------------------------------------------------------------------
def test_compute_rsi_full_raises_on_ohlc_length_mismatch():
    open_ = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    high = np.array([1.0, 2.0, 3.0, 4.0])  # 長さ不一致
    low = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    close = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    with pytest.raises(ValueError):
        compute_rsi_full(open_, high, low, close, rsi_period=3)


# ---------------------------------------------------------------------------
# TC-14 RsiResult は不変（rsi / ma が writeable=False）
# ---------------------------------------------------------------------------
def test_rsi_result_arrays_are_immutable():
    open_, high, low, close = _ohlc8()
    result = compute_rsi_full(open_, high, low, close, rsi_period=3, ma_period=2)
    with pytest.raises(ValueError):
        result.rsi[0] = 1.0
    with pytest.raises(ValueError):
        result.ma[0] = 1.0


# ---------------------------------------------------------------------------
# TC-15 既定パラメータ（DEFAULT_RSI_PERIOD=6 / DEFAULT_MA_PERIOD=5 / DEFAULT_APPLY=5）
# ---------------------------------------------------------------------------
def test_default_parameters_match_source_inputs():
    assert DEFAULT_RSI_PERIOD == 6
    assert DEFAULT_MA_PERIOD == 5
    assert DEFAULT_APPLY == 5
