"""PRO!fitSTC（PRO!fitOscillator）core 層（純粋計算）の検証。

元 MQL4 ``PRO!fitSTC.mq4`` を昇順（古→新）へ 1:1 変換した一意定義を固定する。

オシレーター本体（PRO!fitSTC.mq4 L104）::

    ExtBufferOscillator[i] = iStochastic(
        NULL, 0, inpPeriodOscillator, 1, 1, MODE_EMA, 0, MODE_MAIN, i);

slowing=1 / Dperiod=1 のため MODE_EMA 平滑は恒等（vestigial）。price_field=0 は
Low/High でレンジを取る。直近 period 本（現足含む）について::

    LL = min(low[a-period+1 .. a]),  HH = max(high[a-period+1 .. a])
    %K[a] = 100 * (close[a] - LL) / (HH - LL)

- warm-up（a < period-1）: 元 iStochastic 既定どおり 0（NaN ではない）。
- ゼロ割（HH == LL）: 0（spec 確定）。
- period < 2: ValueError（元 OnInit の INIT_FAILED 相当）。

水準線（PRO!fitSTC.mq4 L108-111, iBandsOnArray, period=rates_total=全長）::

    P1 = mean + 1.00*std,  P2 = mean + 1.96*std   （MODE_UPPER, dev=1.00/1.96）
    M1 = mean - 1.00*std,  M2 = mean - 1.96*std   （MODE_LOWER, dev=1.00/1.96）
    sub_min = M2,  sub_max = P2                    （INDICATOR_MINIMUM/MAXIMUM）

mean は全系列平均（warm-up の 0 込み）、std は母標準偏差（÷N・warm-up の 0 込み）。
warm-up の 0 を除外せず 1:1 再現する（除外は禁止）。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    DEFAULT_PERIOD,
    StcResult,
    compute_osc_levels,
    compute_stc,
    compute_stochastic,
)


# ---------------------------------------------------------------------------
# compute_stochastic — %K 手計算（period=3, N=6・直近3本・現足含む）
# ---------------------------------------------------------------------------
def test_compute_stochastic_handcalc_period3():
    """period=3 の %K を手計算値で 1:1 固定する。

    high =[10,12,11,15,14,20], low=[0,2,1,3,4,5], close=[5,8,6,10,9,18]:
        i<2 -> warm-up 0
        i=2: LL=min(0,2,1)=0,  HH=max(10,12,11)=12 -> 100*(6-0)/12   = 50.0
        i=3: LL=min(2,1,3)=1,  HH=max(12,11,15)=15 -> 100*(10-1)/14  = 64.285714...
        i=4: LL=min(1,3,4)=1,  HH=max(11,15,14)=15 -> 100*(9-1)/14   = 57.142857...
        i=5: LL=min(3,4,5)=3,  HH=max(15,14,20)=20 -> 100*(18-3)/17  = 88.235294...
    """
    # Arrange
    high = np.array([10.0, 12.0, 11.0, 15.0, 14.0, 20.0])
    low = np.array([0.0, 2.0, 1.0, 3.0, 4.0, 5.0])
    close = np.array([5.0, 8.0, 6.0, 10.0, 9.0, 18.0])

    # Act
    k = compute_stochastic(high, low, close, period=3)

    # Assert
    np.testing.assert_allclose(
        k,
        [0.0, 0.0, 50.0, 100.0 * 9 / 14, 100.0 * 8 / 14, 100.0 * 15 / 17],
    )


def test_compute_stochastic_warmup_is_zero_not_nan():
    """warm-up（a < period-1）は 0（NaN ではない。元 iStochastic 既定）。"""
    # Arrange
    high = np.array([10.0, 11.0, 12.0])
    low = np.array([0.0, 1.0, 2.0])
    close = np.array([5.0, 6.0, 7.0])

    # Act
    k = compute_stochastic(high, low, close, period=3)

    # Assert
    assert k[0] == 0.0
    assert k[1] == 0.0
    assert not np.isnan(k[0])
    assert not np.isnan(k[1])


def test_compute_stochastic_zero_division_is_zero():
    """ゼロ割（HH == LL・レンジ 0）は 0（spec 確定。100 ではない）。"""
    # Arrange: high==low==const の区間
    high = np.array([5.0, 5.0, 5.0])
    low = np.array([5.0, 5.0, 5.0])
    close = np.array([5.0, 5.0, 5.0])

    # Act
    k = compute_stochastic(high, low, close, period=2)

    # Assert
    assert k[1] == 0.0
    assert k[2] == 0.0


def test_compute_stochastic_close_at_high_is_100():
    """終値がレンジ上端なら %K=100。"""
    high = np.array([10.0, 12.0, 12.0])
    low = np.array([0.0, 2.0, 2.0])
    close = np.array([0.0, 0.0, 12.0])
    k = compute_stochastic(high, low, close, period=2)
    assert k[2] == pytest.approx(100.0)


def test_compute_stochastic_close_at_low_is_0():
    """終値がレンジ下端なら %K=0。"""
    high = np.array([10.0, 10.0])
    low = np.array([3.0, 3.0])
    close = np.array([10.0, 3.0])
    k = compute_stochastic(high, low, close, period=2)
    assert k[1] == pytest.approx(0.0)


def test_compute_stochastic_invalid_period_raises():
    """period<2 は元 OnInit（inpPeriodOscillator<2 → INIT_FAILED）に対応し ValueError。"""
    high = np.array([1.0, 2.0])
    low = np.array([0.0, 1.0])
    close = np.array([0.5, 1.5])
    with pytest.raises(ValueError):
        compute_stochastic(high, low, close, period=1)


def test_compute_stochastic_length_mismatch_raises():
    """high/low/close の長さ不一致は ValueError。"""
    with pytest.raises(ValueError):
        compute_stochastic(
            np.array([1.0, 2.0]), np.array([0.0]), np.array([1.0, 2.0]), period=2
        )


def test_compute_stochastic_empty_returns_empty():
    """空入力は空配列を返す（例外でなく定義挙動）。"""
    out = compute_stochastic(np.array([]), np.array([]), np.array([]), period=2)
    assert out.shape == (0,)


# ---------------------------------------------------------------------------
# compute_osc_levels — iBandsOnArray（全系列・warm-up の 0 込み）
# ---------------------------------------------------------------------------
def test_compute_osc_levels_includes_warmup_zeros_discriminating():
    """mean/母σ を全系列（warm-up の 0 込み）で固定し、0 除外版と区別する。

    osc=[0,0,50,100]（warm-up 2 本の 0 を含む）:
        全系列込み: mean=37.5, std=sqrt(mean((x-37.5)^2))=41.4578...
            P1=mean+1.00σ=78.9578..., P2=mean+1.96σ=118.7573...,
            M1=mean-1.00σ=-3.9578..., M2=mean-1.96σ=-43.7573...
        0 除外版（禁止・WRONG）なら mean=75 で値が変わる → discriminating。
    """
    # Arrange
    osc = np.array([0.0, 0.0, 50.0, 100.0])
    mean = 37.5
    std = float(np.sqrt(np.mean((osc - mean) ** 2)))  # 41.45780988

    # Act
    levels = compute_osc_levels(osc)

    # Assert: 全系列込みの母σ。0 除外版だと不一致になる discriminating な固定値。
    assert levels["P1"] == pytest.approx(mean + 1.00 * std)
    assert levels["P2"] == pytest.approx(mean + 1.96 * std)
    assert levels["M1"] == pytest.approx(mean - 1.00 * std)
    assert levels["M2"] == pytest.approx(mean - 1.96 * std)
    # 0 除外版（mean=75）では P1 が 78.957 にならないことを明示
    assert levels["P1"] != pytest.approx(75.0 + 1.00 * float(
        np.sqrt(np.mean((np.array([50.0, 100.0]) - 75.0) ** 2))
    ))


def test_compute_osc_levels_keys_are_p_m_only():
    """4 水準キーは P1/P2/M1/M2（upper_*/lower_* ではない）。"""
    osc = np.array([0.0, 10.0, 20.0, 30.0, 40.0])
    levels = compute_osc_levels(osc)
    assert set(levels.keys()) == {"P1", "P2", "M1", "M2"}


# ---------------------------------------------------------------------------
# compute_stc — 統合 frozen DTO（StcResult）
# ---------------------------------------------------------------------------
def test_compute_stc_oscillator_equals_stochastic():
    """StcResult.oscillator は compute_stochastic（生 %K）と一致する。"""
    # Arrange
    high = np.array([10.0, 12.0, 11.0, 15.0, 14.0, 20.0])
    low = np.array([0.0, 2.0, 1.0, 3.0, 4.0, 5.0])
    close = np.array([5.0, 8.0, 6.0, 10.0, 9.0, 18.0])

    # Act
    result = compute_stc(high, low, close, period=3)
    k = compute_stochastic(high, low, close, period=3)

    # Assert
    np.testing.assert_array_equal(result.oscillator, k)


def test_compute_stc_levels_and_sub_range():
    """StcResult の levels(P1/P2/M1/M2)・sub_min(=M2)・sub_max(=P2) を固定。"""
    # Arrange
    high = np.array([10.0, 12.0, 11.0, 15.0, 14.0, 20.0])
    low = np.array([0.0, 2.0, 1.0, 3.0, 4.0, 5.0])
    close = np.array([5.0, 8.0, 6.0, 10.0, 9.0, 18.0])

    # Act
    result = compute_stc(high, low, close, period=3)
    expected = compute_osc_levels(result.oscillator)

    # Assert
    assert result.levels == pytest.approx(expected)
    assert set(result.levels.keys()) == {"P1", "P2", "M1", "M2"}
    assert result.sub_min == pytest.approx(expected["M2"])
    assert result.sub_max == pytest.approx(expected["P2"])


def test_compute_stc_is_frozen_dto():
    """StcResult は frozen（属性再代入で例外）。"""
    high = np.array([10.0, 12.0, 11.0])
    low = np.array([0.0, 2.0, 1.0])
    close = np.array([5.0, 8.0, 6.0])
    result = compute_stc(high, low, close, period=2)
    with pytest.raises(Exception):
        result.sub_min = 0.0  # type: ignore[misc]


def test_compute_stc_oscillator_is_readonly():
    """StcResult.oscillator は writeable=False（profit_adx_needle 準拠の不変性）。"""
    high = np.array([10.0, 12.0, 11.0])
    low = np.array([0.0, 2.0, 1.0])
    close = np.array([5.0, 8.0, 6.0])
    result = compute_stc(high, low, close, period=2)
    assert result.oscillator.flags.writeable is False
    with pytest.raises(ValueError):
        result.oscillator[0] = 999.0


def test_compute_stc_default_period_is_70():
    """既定 period は元 input inpPeriodOscillator=70。"""
    assert DEFAULT_PERIOD == 70


def test_compute_stc_length_mismatch_raises():
    """high/low/close 長不一致は ValueError（統合層でも伝播）。"""
    with pytest.raises(ValueError):
        compute_stc(
            np.array([1.0, 2.0]), np.array([0.0]), np.array([1.0, 2.0]), period=2
        )
