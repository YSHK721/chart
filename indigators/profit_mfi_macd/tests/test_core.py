"""PRO!fitMFIMACD core 層（純粋計算）の検証。

元 MQL4 ``PRO!fitMFIMACD.mq4`` の iMFI(MFIperiod) → EMA(FastEMA)/EMA(SlowEMA)
→ macd=fast-slow → signal=EMA(macd,SignalEMA) → histogram=2.618*(macd-signal)
→ σ7水準（histogram 全系列・母σ÷N）を昇順（古→新, index 0=最古）へ 1:1 変換した
一意定義を固定する。

確定セマンティクス（依頼仕様, extern MFIperiod=13/FastEMA=4/SlowEMA=8/SignalEMA=4）::

    mfi   = compute_mfi(high,low,close,volume,period=13)   # iMFI 複製
    fast  = EMA(mfi, 4) ; slow = EMA(mfi, 8)               # 共有 on_buffer
    macd[i]      = fast[i] - slow[i]
    signal       = EMA(macd, 4)
    histogram[i] = 2.618 * (macd[i] - signal[i])           # 係数 2.618 厳密
    σ7水準は histogram（=2.618 適用後）全系列:
        avg=mean(histogram), sigma=sqrt(mean((histogram-avg)**2))  # 母σ÷N
        {p1:avg+σ,p2:avg+2σ,p3:avg+3σ,m1:avg-σ,m2:avg-2σ,m3:avg-3σ,mid50:50}

iMFI（負MF==0→100・同値非加算・warm-up0）は profit_mfi の compute_mfi と同一挙動。
EMA は共有 moving_averages.exponential_ma_on_buffer を再利用する。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    DEFAULT_FAST_EMA,
    DEFAULT_MFI_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    MfiMacdResult,
    compute_mfi,
    compute_mfimacd,
    compute_mfimacd_levels,
)

# 共有 EMA 関数（照合用）。
from moving_averages import exponential_ma_on_buffer  # noqa: E402


# 共通の検証用 OHLCV（period=3 で discriminating な系列）。
_HIGH = np.array([10.0, 11.0, 12.0, 11.0, 13.0, 13.0, 14.0, 12.0])
_LOW = np.array([8.0, 9.0, 10.0, 9.0, 11.0, 11.0, 12.0, 10.0])
_CLOSE = np.array([9.0, 10.0, 11.0, 10.0, 12.0, 12.0, 13.0, 11.0])
_VOLUME = np.array([100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0])


def _shared_ema(price: np.ndarray, period: int) -> np.ndarray:
    """共有 exponential_ma_on_buffer を 0 初期化 buffer で適用した結果を返す。"""
    buf = np.zeros(price.shape[0], dtype=np.float64)
    exponential_ma_on_buffer(price.shape[0], 0, 0, period, price, buf)
    return buf


# ---------------------------------------------------------------------------
# TC-01 iMFI 複製: profit_mfi と同一の手計算固定（period=3, N=6・正負分類）
# ---------------------------------------------------------------------------
def test_compute_mfi_returns_hand_calculated_values_for_period3_n6():
    # Arrange: TP=[9,10,11,10,12,12], MF=TP*vol。
    high = np.array([10.0, 11.0, 12.0, 11.0, 13.0, 13.0])
    low = np.array([8.0, 9.0, 10.0, 9.0, 11.0, 11.0])
    close = np.array([9.0, 10.0, 11.0, 10.0, 12.0, 12.0])
    volume = np.array([100.0, 200.0, 300.0, 400.0, 500.0, 600.0])
    # i=3 win[1..3]: pos=MF1+MF2=2000+3300=5300, neg=MF3=4000 -> 100*5300/9300
    # i=4 win[2..4]: pos=MF2+MF4=3300+6000=9300, neg=MF3=4000 -> 100*9300/13300
    # i=5 win[3..5]: TP5==TP4(=12)->non-add; pos=MF4=6000, neg=MF3=4000 -> 60.0
    expected = np.array(
        [0.0, 0.0, 0.0, 100.0 * 5300.0 / 9300.0, 100.0 * 9300.0 / 13300.0, 60.0]
    )

    # Act
    result = compute_mfi(high, low, close, volume, period=3)

    # Assert
    np.testing.assert_allclose(result, expected, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-02 iMFI 複製: 負MF==0 -> 100（all-up window）
# ---------------------------------------------------------------------------
def test_compute_mfi_returns_100_when_negative_mf_is_zero():
    # Arrange: 単調増加 TP -> 全 up -> 負MF==0。
    high = np.array([1.0, 2.0, 3.0, 4.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([10.0, 10.0, 10.0, 10.0])

    # Act
    result = compute_mfi(high, low, close, volume, period=2)

    # Assert: i>=2 は 100（負MF==0 是正済み挙動）。
    assert result[2] == 100.0
    assert result[3] == 100.0


# ---------------------------------------------------------------------------
# TC-03 iMFI 複製: 同値の非対称（TP[j]==TP[j-1] は正にも負にも加算しない）
# ---------------------------------------------------------------------------
def test_compute_mfi_excludes_equal_tp_bars_from_both_pos_and_neg():
    # Arrange: TP=[10,12,12,8], period=3, i=3 win[1..3]。
    #   j=1: 12>10 -> pos+=MF1=1200 ; j=2: 12==12 -> 加算しない ; j=3: 8<12 -> neg+=MF3=400
    #   MFI = 100*1200/(1200+400) = 75.0
    high = np.array([10.0, 12.0, 12.0, 8.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([1.0, 100.0, 999.0, 50.0])

    # Act
    result = compute_mfi(high, low, close, volume, period=3)

    # Assert
    assert result[3] == pytest.approx(75.0, rel=1e-12)


# ---------------------------------------------------------------------------
# TC-04 iMFI 複製: warm-up 0（i < period は 0, NaN ではない）
# ---------------------------------------------------------------------------
def test_compute_mfi_returns_zero_for_warmup_bars_below_period():
    high = np.array([10.0, 11.0, 12.0, 13.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([10.0, 10.0, 10.0, 10.0])

    result = compute_mfi(high, low, close, volume, period=3)

    assert result[0] == 0.0
    assert result[1] == 0.0
    assert result[2] == 0.0
    assert not np.isnan(result).any()


# ---------------------------------------------------------------------------
# TC-05 EMA 連鎖: fast/slow が共有 exponential_ma_on_buffer 出力と一致
# ---------------------------------------------------------------------------
def test_compute_mfimacd_fast_slow_match_shared_exponential_ma_on_buffer():
    # Arrange
    mfi = compute_mfi(_HIGH, _LOW, _CLOSE, _VOLUME, period=3)
    expected_fast = _shared_ema(mfi, 4)
    expected_slow = _shared_ema(mfi, 8)

    # Act
    result = compute_mfimacd(
        _HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=3, fast=4, slow=8, signal=4
    )

    # Assert
    np.testing.assert_allclose(result.fast, expected_fast, rtol=1e-12)
    np.testing.assert_allclose(result.slow, expected_slow, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-06 macd = fast - slow
# ---------------------------------------------------------------------------
def test_compute_mfimacd_macd_equals_fast_minus_slow():
    # Arrange
    result = compute_mfimacd(
        _HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=3, fast=4, slow=8, signal=4
    )

    # Act / Assert
    np.testing.assert_allclose(
        result.macd, result.fast - result.slow, rtol=1e-12
    )


# ---------------------------------------------------------------------------
# TC-07 signal = EMA(macd, SignalEMA)
# ---------------------------------------------------------------------------
def test_compute_mfimacd_signal_equals_ema_of_macd():
    # Arrange
    result = compute_mfimacd(
        _HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=3, fast=4, slow=8, signal=4
    )
    expected_signal = _shared_ema(result.macd, 4)

    # Act / Assert
    np.testing.assert_allclose(result.signal, expected_signal, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-08 histogram = 2.618 * (macd - signal)（係数 2.618 厳密・discriminating）
# ---------------------------------------------------------------------------
def test_compute_mfimacd_histogram_applies_exact_2618_coefficient():
    # Arrange
    result = compute_mfimacd(
        _HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=3, fast=4, slow=8, signal=4
    )
    diff = result.macd - result.signal
    expected_hist = 2.618 * diff

    # Act / Assert: 係数 2.618 厳密。
    np.testing.assert_allclose(result.histogram, expected_hist, rtol=1e-12)
    # 係数 1.0（=macd-signal そのもの）にすると fail する discriminating 固定。
    #   diff が非ゼロな要素が存在することを前提に、係数差を検出する。
    assert np.any(diff != 0.0)
    assert not np.allclose(result.histogram, diff, rtol=1e-9)


# ---------------------------------------------------------------------------
# TC-09 σ順序: σ/avg は histogram（2.618 適用後）に掛かる（discriminating）
# ---------------------------------------------------------------------------
def test_compute_mfimacd_levels_use_histogram_after_2618_not_before():
    # Arrange
    result = compute_mfimacd(
        _HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=3, fast=4, slow=8, signal=4
    )
    hist = result.histogram
    diff = result.macd - result.signal  # 係数適用前（誤った母集団）
    avg_hist = float(np.mean(hist))
    sigma_hist = float(np.sqrt(np.mean((hist - avg_hist) ** 2)))
    avg_diff = float(np.mean(diff))
    sigma_diff = float(np.sqrt(np.mean((diff - avg_diff) ** 2)))
    # 2.618 倍で avg/σ は定数倍されるため両者は異なる（discriminating 前提）。
    assert avg_hist != pytest.approx(avg_diff, rel=1e-9) or sigma_hist != pytest.approx(
        sigma_diff, rel=1e-9
    )

    # Act
    levels = result.levels

    # Assert: histogram（係数適用後）に掛かること。
    assert levels["p1"] == pytest.approx(avg_hist + sigma_hist, rel=1e-12)
    assert levels["m3"] == pytest.approx(avg_hist - 3 * sigma_hist, rel=1e-12)
    # 係数適用前 diff に掛けていると p1 が avg_diff+sigma_diff になり fail する。
    assert levels["p1"] != pytest.approx(avg_diff + sigma_diff, rel=1e-9)


# ---------------------------------------------------------------------------
# TC-10 σ7水準: 母σ÷N（標本σ ddof=1 では fail）＋ mid50=50
# ---------------------------------------------------------------------------
def test_compute_mfimacd_levels_uses_population_std_over_full_histogram():
    # Arrange
    result = compute_mfimacd(
        _HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=3, fast=4, slow=8, signal=4
    )
    hist = result.histogram
    avg = float(np.mean(hist))
    sigma_pop = float(np.sqrt(np.mean((hist - avg) ** 2)))  # ÷N（母σ）
    sigma_sample = float(np.std(hist, ddof=1))  # ÷(N-1)（標本σ）
    assert sigma_pop != sigma_sample  # discriminating 前提

    # Act
    levels = compute_mfimacd_levels(hist)

    # Assert: 7 水準＝avg±1/2/3σ＋mid50=50（母σ使用）。
    assert levels["p1"] == pytest.approx(avg + sigma_pop, rel=1e-12)
    assert levels["p2"] == pytest.approx(avg + 2 * sigma_pop, rel=1e-12)
    assert levels["p3"] == pytest.approx(avg + 3 * sigma_pop, rel=1e-12)
    assert levels["m1"] == pytest.approx(avg - sigma_pop, rel=1e-12)
    assert levels["m2"] == pytest.approx(avg - 2 * sigma_pop, rel=1e-12)
    assert levels["m3"] == pytest.approx(avg - 3 * sigma_pop, rel=1e-12)
    assert levels["mid50"] == 50.0
    # 標本σを誤用していると p1 が avg+sigma_sample になり fail する。
    assert levels["p1"] != pytest.approx(avg + sigma_sample, rel=1e-12)


# ---------------------------------------------------------------------------
# TC-11 levels が result から取得でき 7 キーである
# ---------------------------------------------------------------------------
def test_compute_mfimacd_result_levels_has_seven_keys():
    result = compute_mfimacd(
        _HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=3, fast=4, slow=8, signal=4
    )
    assert set(result.levels.keys()) == {
        "p1",
        "p2",
        "p3",
        "m1",
        "m2",
        "m3",
        "mid50",
    }


# ---------------------------------------------------------------------------
# TC-12 例外: mfi_period < 2 -> ValueError
# ---------------------------------------------------------------------------
def test_compute_mfimacd_raises_valueerror_for_mfi_period_below_2():
    with pytest.raises(ValueError):
        compute_mfimacd(_HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=1)


# ---------------------------------------------------------------------------
# TC-13 例外: 長さ不一致 -> ValueError
# ---------------------------------------------------------------------------
def test_compute_mfimacd_raises_valueerror_for_length_mismatch():
    bad_low = _LOW[:-1]  # 長さ不一致
    with pytest.raises(ValueError):
        compute_mfimacd(_HIGH, bad_low, _CLOSE, _VOLUME, mfi_period=3)


# ---------------------------------------------------------------------------
# TC-14 DTO 不変性: 全 ndarray writeable=False, frozen
# ---------------------------------------------------------------------------
def test_compute_mfimacd_returns_frozen_dto_with_readonly_arrays():
    # Arrange / Act
    result = compute_mfimacd(
        _HIGH, _LOW, _CLOSE, _VOLUME, mfi_period=3, fast=4, slow=8, signal=4
    )

    # Assert: 中間 mfi/fast/slow も含む全 ndarray が読み取り専用。
    for name in ("mfi", "fast", "slow", "macd", "signal", "histogram"):
        arr = getattr(result, name)
        assert arr.flags.writeable is False, name
        with pytest.raises(ValueError):
            arr[0] = 1.0
    # frozen DTO: フィールド再代入は不可。
    assert isinstance(result, MfiMacdResult)
    with pytest.raises(Exception):
        result.histogram = np.zeros(len(_CLOSE))


# ---------------------------------------------------------------------------
# TC-15 DEFAULT 定数（13/4/8/4）
# ---------------------------------------------------------------------------
def test_default_constants_match_extern_inputs():
    assert DEFAULT_MFI_PERIOD == 13
    assert DEFAULT_FAST_EMA == 4
    assert DEFAULT_SLOW_EMA == 8
    assert DEFAULT_SIGNAL_EMA == 4
