"""PRO!fitMFI core 層（純粋計算）の検証。

元 MQL4 ``PRO!fitMFI.mq4`` の iMFI（Money Flow Index）＋ EMA 平滑 ＋ σ 水準を
昇順（古→新, index 0=最古）へ 1:1 変換した一意定義を固定する。

確定セマンティクス（依頼仕様）::

    TP[i]  = (high[i] + low[i] + close[i]) / 3
    MF[i]  = TP[i] * volume[i]
    バー i (i>=period) の窓 [i-period+1 .. i] の各 j (j>=1):
        TP[j] > TP[j-1] -> 正MF += MF[j]
        TP[j] < TP[j-1] -> 負MF += MF[j]
        TP[j] == TP[j-1] -> 加算しない（非対称・§4.4）
    MFI[i] = 100 * 正MF / (正MF + 負MF)
        正MF+負MF == 0          -> 0
        負MF == 0 かつ 正MF > 0 -> 100
        正MF == 0 かつ 負MF > 0 -> 0
    warm-up (i < period)        -> 0（NaN ではない。元 iMFI/SetIndexDrawBegin 既定）

EMA 平滑は共有 moving_averages.exponential_ma_on_buffer を再利用する。
σ 水準は全系列（warm-up 0 込み）の平均と母標準偏差（÷N）で算出する。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    MfiResult,
    compute_mfi,
    compute_mfi_full,
    compute_mfi_levels,
)

# 共有 EMA 関数（照合用）。
from moving_averages import exponential_ma_on_buffer  # noqa: E402


# ---------------------------------------------------------------------------
# TC-01 iMFI 手計算固定（period=3, N=6・正負分類）
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
        [
            0.0,
            0.0,
            0.0,
            100.0 * 5300.0 / 9300.0,
            100.0 * 9300.0 / 13300.0,
            60.0,
        ]
    )

    # Act
    result = compute_mfi(high, low, close, volume, period=3)

    # Assert
    np.testing.assert_allclose(result, expected, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-02 warm-up 0（i < period は 0）
# ---------------------------------------------------------------------------
def test_compute_mfi_returns_zero_for_warmup_bars_below_period():
    # Arrange
    high = np.array([10.0, 11.0, 12.0, 13.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([10.0, 10.0, 10.0, 10.0])

    # Act
    result = compute_mfi(high, low, close, volume, period=3)

    # Assert: i=0,1,2 は warm-up で 0（NaN ではない）。
    assert result[0] == 0.0
    assert result[1] == 0.0
    assert result[2] == 0.0
    assert not np.isnan(result).any()


# ---------------------------------------------------------------------------
# TC-03 同値の非対称（TP[j]==TP[j-1] は正にも負にも加算しない）
# ---------------------------------------------------------------------------
def test_compute_mfi_excludes_equal_tp_bars_from_both_pos_and_neg():
    # Arrange: TP=[10,12,12,8], period=3, i=3 win[1..3]。
    #   j=1: 12>10 -> pos+=MF1=1200
    #   j=2: 12==12 -> 加算しない（MF2=11988 は無視されるべき）
    #   j=3: 8<12  -> neg+=MF3=400
    #   MFI = 100*1200/(1200+400) = 75.0
    # MF2 を誤って pos に足すと 97.06、neg に足すと 8.83 となり fail する。
    high = np.array([10.0, 12.0, 12.0, 8.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([1.0, 100.0, 999.0, 50.0])

    # Act
    result = compute_mfi(high, low, close, volume, period=3)

    # Assert
    assert result[3] == pytest.approx(75.0, rel=1e-12)


# ---------------------------------------------------------------------------
# TC-04a ゼロ割系: 負MF==0 かつ 正MF>0 -> 100
# ---------------------------------------------------------------------------
def test_compute_mfi_returns_100_when_negative_mf_is_zero():
    # Arrange: 単調増加 TP -> 全 up -> 負MF==0。
    high = np.array([1.0, 2.0, 3.0, 4.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([10.0, 10.0, 10.0, 10.0])

    # Act
    result = compute_mfi(high, low, close, volume, period=2)

    # Assert: i>=2 は 100。
    assert result[2] == 100.0
    assert result[3] == 100.0


# ---------------------------------------------------------------------------
# TC-04b ゼロ割系: 正MF==0 かつ 負MF>0 -> 0
# ---------------------------------------------------------------------------
def test_compute_mfi_returns_0_when_positive_mf_is_zero():
    # Arrange: 単調減少 TP -> 全 down -> 正MF==0。
    high = np.array([4.0, 3.0, 2.0, 1.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([10.0, 10.0, 10.0, 10.0])

    # Act
    result = compute_mfi(high, low, close, volume, period=2)

    # Assert
    assert result[2] == 0.0
    assert result[3] == 0.0


# ---------------------------------------------------------------------------
# TC-04c ゼロ割系: 正MF+負MF==0（全同値） -> 0
# ---------------------------------------------------------------------------
def test_compute_mfi_returns_100_when_total_mf_is_zero_flat_window():
    # Arrange: 全 TP 同値 -> pos==neg==0（flat window）。
    # 組込 iMFI（MFI.mq5 L107-110 / MFI.mq4 L86-89）は 負MF==0 を一律 100 とするため、
    # flat window（正MF==0 かつ 負MF==0）も 100 を返す（0 ではない）。
    high = np.array([5.0, 5.0, 5.0, 5.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([10.0, 10.0, 10.0, 10.0])

    # Act
    result = compute_mfi(high, low, close, volume, period=2)

    # Assert
    assert result[2] == 100.0
    assert result[3] == 100.0


# ---------------------------------------------------------------------------
# TC-05 period < 2 -> ValueError
# ---------------------------------------------------------------------------
def test_compute_mfi_raises_valueerror_for_period_below_2():
    high = np.array([1.0, 2.0, 3.0])
    low = high.copy()
    close = high.copy()
    volume = np.array([1.0, 1.0, 1.0])

    with pytest.raises(ValueError):
        compute_mfi(high, low, close, volume, period=1)


# ---------------------------------------------------------------------------
# TC-06 長さ不一致 -> ValueError
# ---------------------------------------------------------------------------
def test_compute_mfi_raises_valueerror_for_length_mismatch():
    high = np.array([1.0, 2.0, 3.0])
    low = np.array([1.0, 2.0])  # 長さ不一致
    close = np.array([1.0, 2.0, 3.0])
    volume = np.array([1.0, 1.0, 1.0])

    with pytest.raises(ValueError):
        compute_mfi(high, low, close, volume, period=2)


# ---------------------------------------------------------------------------
# TC-07 EMA 一致（共有 exponential_ma_on_buffer の出力と一致）
# ---------------------------------------------------------------------------
def test_compute_mfi_full_ma_matches_shared_exponential_ma_on_buffer():
    # Arrange
    high = np.array([10.0, 11.0, 12.0, 11.0, 13.0, 13.0])
    low = np.array([8.0, 9.0, 10.0, 9.0, 11.0, 11.0])
    close = np.array([9.0, 10.0, 11.0, 10.0, 12.0, 12.0])
    volume = np.array([100.0, 200.0, 300.0, 400.0, 500.0, 600.0])
    mfi = compute_mfi(high, low, close, volume, period=3)
    expected_ma = np.zeros(len(mfi))
    exponential_ma_on_buffer(len(mfi), 0, 0, 5, mfi, expected_ma)

    # Act
    result = compute_mfi_full(
        high, low, close, volume, mfi_period=3, ma_period=5
    )

    # Assert
    np.testing.assert_allclose(result.ma, expected_ma, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-08 EMA period<=1 は moving_averages 挙動（未計算 0 返し）に従う
# ---------------------------------------------------------------------------
def test_compute_mfi_full_ma_is_all_zero_when_ma_period_below_2():
    # Arrange: ma_period=1 -> exponential_ma_on_buffer は 0 を返し buffer 未更新。
    #   実装が buffer を 0 初期化していれば ma は全 0 のまま。
    high = np.array([10.0, 11.0, 12.0, 11.0, 13.0, 13.0])
    low = np.array([8.0, 9.0, 10.0, 9.0, 11.0, 11.0])
    close = np.array([9.0, 10.0, 11.0, 10.0, 12.0, 12.0])
    volume = np.array([100.0, 200.0, 300.0, 400.0, 500.0, 600.0])

    # Act
    result = compute_mfi_full(
        high, low, close, volume, mfi_period=3, ma_period=1
    )

    # Assert
    np.testing.assert_array_equal(result.ma, np.zeros(len(close)))


# ---------------------------------------------------------------------------
# TC-09 全系列母σ÷N（標本σ ddof=1 とは異なる discriminating 入力）
# ---------------------------------------------------------------------------
def test_compute_mfi_levels_uses_population_std_over_full_series():
    # Arrange: EMA 系列を直接与える代わりに compute_mfi_full の ma を使う。
    high = np.array([10.0, 11.0, 12.0, 11.0, 13.0, 13.0])
    low = np.array([8.0, 9.0, 10.0, 9.0, 11.0, 11.0])
    close = np.array([9.0, 10.0, 11.0, 10.0, 12.0, 12.0])
    volume = np.array([100.0, 200.0, 300.0, 400.0, 500.0, 600.0])
    ma = compute_mfi_full(
        high, low, close, volume, mfi_period=3, ma_period=5
    ).ma
    avg = float(np.mean(ma))
    sigma_pop = float(np.sqrt(np.mean((ma - avg) ** 2)))  # ÷N（母σ）
    sigma_sample = float(np.std(ma, ddof=1))  # ÷(N-1)（標本σ）
    assert sigma_pop != sigma_sample  # discriminating 前提

    # Act
    levels = compute_mfi_levels(ma)

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
# TC-10 DTO 不変性（mfi/ma writeable=False, frozen）
# ---------------------------------------------------------------------------
def test_compute_mfi_full_returns_frozen_dto_with_readonly_arrays():
    # Arrange
    high = np.array([10.0, 11.0, 12.0, 11.0, 13.0, 13.0])
    low = np.array([8.0, 9.0, 10.0, 9.0, 11.0, 11.0])
    close = np.array([9.0, 10.0, 11.0, 10.0, 12.0, 12.0])
    volume = np.array([100.0, 200.0, 300.0, 400.0, 500.0, 600.0])

    # Act
    result = compute_mfi_full(
        high, low, close, volume, mfi_period=3, ma_period=5
    )

    # Assert: 配列は読み取り専用。
    assert result.mfi.flags.writeable is False
    assert result.ma.flags.writeable is False
    with pytest.raises(ValueError):
        result.mfi[0] = 1.0
    with pytest.raises(ValueError):
        result.ma[0] = 1.0
    # frozen DTO: フィールド再代入は不可。
    assert isinstance(result, MfiResult)
    with pytest.raises(Exception):
        result.mfi = np.zeros(6)
    # levels は 7 要素。
    assert set(result.levels.keys()) == {
        "p1",
        "p2",
        "p3",
        "m1",
        "m2",
        "m3",
        "mid50",
    }
