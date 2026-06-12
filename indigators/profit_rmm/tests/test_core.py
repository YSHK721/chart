"""PRO!fitRMM core 層（純粋計算）の検証。

元 MQL4 ``PRO!fitRMM.mq4``（複合レベルカウント指標）が呼ぶ iRSI / iWPR / iMFI /
MAROD を funLevelCount で合算する処理を、昇順（古→新, index 0=最古）へ 1:1 変換した
一意定義を固定する。

固定する discriminating 観点（依頼仕様 §TDD 1..10）::

    1. iWPR 権威一致（WPR.mq5: maxH/minL 窓・flat→前値・warm-up i<period-1・range[-100,0]）
    3. MAROD = (typical-ma)/ma*100（手計算）
    4. oscillator_span のクランプ非対称（clamp=True で x3p>100/x3m<0 クランプ・clamp=False 素値）
    5. level_count_score 4 ケース手計算（case0/1 の (span-50)/200・case2/3 の (span/2)/200・符号）
    6. 合算（4 採点の和＝level_count・warm-up バー寄与込み）
    7. LC の σ6 水準（母σ÷N・6 本）
    8. 退化ゼロ割（span==50 で inf or nan・ガードしない 1:1）
    9. 例外（osc_period<2→ValueError・長不一致→ValueError）
   10. DTO 不変性（全 ndarray writeable=False・frozen）
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = profit_rmm/

from src import core  # noqa: E402


# ---------------------------------------------------------------------------
# 観点 1: iWPR 権威一致（WPR.mq5 手計算）
# ---------------------------------------------------------------------------
class TestComputeWpr:
    def test_compute_wpr_matches_authority_handcalc_for_period3(self) -> None:
        # Arrange: 権威 WPR.mq5 を period=3 で手計算した期待値。
        #   warm-up i<period-1=2 -> 0。i=2: max(10,12,11)=12,min(8,9,7)=7,close=10
        #     -> -(12-10)*100/(12-7) = -40。i=3: max(12,11,13)=13,min(9,7,10)=7,close=12
        #     -> -(13-12)*100/(13-7) = -100/6。i=4: max(11,13,9)=13,min(7,10,6)=6,close=8
        #     -> -(13-8)*100/(13-6) = -500/7。
        high = np.array([10.0, 12.0, 11.0, 13.0, 9.0])
        low = np.array([8.0, 9.0, 7.0, 10.0, 6.0])
        close = np.array([9.0, 11.0, 10.0, 12.0, 8.0])
        # Act
        wpr = core.compute_wpr(high, low, close, period=3)
        # Assert
        expected = np.array([0.0, 0.0, -40.0, -100.0 / 6.0, -500.0 / 7.0])
        np.testing.assert_allclose(wpr, expected)

    def test_compute_wpr_warmup_is_period_minus_one_not_period(self) -> None:
        # Arrange: warm-up は i<period-1（iRSI/iMFI の i<period とは 1 本ズレる）。
        #   period=3 のとき i=0,1 が 0、最初の有効値は i=2。
        high = np.array([10.0, 12.0, 11.0, 13.0])
        low = np.array([8.0, 9.0, 7.0, 10.0])
        close = np.array([9.0, 11.0, 10.0, 12.0])
        # Act
        wpr = core.compute_wpr(high, low, close, period=3)
        # Assert: i=2 は非ゼロ（有効値開始）、i<2 は 0。
        assert wpr[0] == 0.0
        assert wpr[1] == 0.0
        assert wpr[2] != 0.0  # i=period-1 で有効値開始（iRSI なら i=period=3 で開始）

    def test_compute_wpr_flat_window_carries_previous_value(self) -> None:
        # Arrange: maxH==minL の窓では前値（wpr[i-1]）を引き継ぐ。
        high = np.array([5.0, 5.0, 5.0, 5.0])
        low = np.array([5.0, 5.0, 5.0, 5.0])
        close = np.array([5.0, 5.0, 5.0, 5.0])
        # Act
        wpr = core.compute_wpr(high, low, close, period=3)
        # Assert: 全 flat -> i=2 は wpr[1]=0、i=3 は wpr[2]=0 を引き継ぐ。
        np.testing.assert_array_equal(wpr, np.array([0.0, 0.0, 0.0, 0.0]))

    def test_compute_wpr_returns_all_zero_when_n_less_than_period(self) -> None:
        # Arrange: n<period -> 全 0（WPR.mq5: rates_total<period -> return 0）。
        high = np.array([10.0, 12.0])
        low = np.array([8.0, 9.0])
        close = np.array([9.0, 11.0])
        # Act
        wpr = core.compute_wpr(high, low, close, period=3)
        # Assert
        np.testing.assert_array_equal(wpr, np.zeros(2))

    def test_compute_wpr_range_is_within_minus_100_and_zero(self) -> None:
        # Arrange: 生 WPR は [-100, 0] の範囲（権威 WPR.mq5）。
        rng = np.random.default_rng(0)
        high = np.cumsum(rng.random(40)) + 10.0
        low = high - rng.random(40) - 0.5
        close = low + rng.random(40) * (high - low)
        # Act
        wpr = core.compute_wpr(high, low, close, period=6)
        # Assert
        assert wpr.min() >= -100.0 - 1e-9
        assert wpr.max() <= 0.0 + 1e-9


# ---------------------------------------------------------------------------
# 観点 3: MAROD = (typical-ma)/ma*100
# ---------------------------------------------------------------------------
class TestComputeMarod:
    def test_compute_marod_handcalc(self) -> None:
        # Arrange: (typical-ma)/ma*100（float 精度・int 切り捨て無し）。
        typical = np.array([12.0, 9.0, 10.0])
        ma = np.array([10.0, 12.0, 8.0])
        # Act
        marod = core.compute_marod(typical, ma)
        # Assert: (12-10)/10*100=20, (9-12)/12*100=-25, (10-8)/8*100=25
        np.testing.assert_allclose(marod, np.array([20.0, -25.0, 25.0]))


# ---------------------------------------------------------------------------
# 観点 4: oscillator_span のクランプ非対称
# ---------------------------------------------------------------------------
class TestOscillatorSpan:
    def test_oscillator_span_clamps_when_clamp_true(self) -> None:
        # Arrange: avg±3σ が [0,100] を超える discriminating 入力。
        #   x=[0,100] -> avg=50, dev=50, x3p=50+150=200->clamp 100, x3m=50-150=-100->clamp 0
        #   span = 100 - 0 = 100。
        x = np.array([0.0, 100.0])
        # Act
        span = core.oscillator_span(x, clamp=True)
        # Assert
        assert span == pytest.approx(100.0)

    def test_oscillator_span_no_clamp_when_clamp_false(self) -> None:
        # Arrange: 同入力で clamp=False -> 素値 x3p-x3m=200-(-100)=300。
        x = np.array([0.0, 100.0])
        # Act
        span = core.oscillator_span(x, clamp=False)
        # Assert: クランプされない（300）。clamp=True なら 100 で fail する discriminating。
        assert span == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# 観点 5: level_count_score 4 ケース手計算
# ---------------------------------------------------------------------------
class TestLevelCountScore:
    def test_case0_handcalc(self) -> None:
        # span=250 -> r=(250-50)/200=1.0; osi=70 -> ((70-50)/1)/100=0.2
        assert core.level_count_score(70.0, 250.0, 0) == pytest.approx(0.2)

    def test_case1_handcalc(self) -> None:
        # span=250 -> r=1.0; osi=30 -> -((50-30)/1)/100=-0.2
        assert core.level_count_score(30.0, 250.0, 1) == pytest.approx(-0.2)

    def test_case2_handcalc(self) -> None:
        # span=400 -> r=(400/2)/200=1.0; osi=10 -> ((10-1)/1)/100=0.09
        assert core.level_count_score(10.0, 400.0, 2) == pytest.approx(0.09)

    def test_case3_handcalc(self) -> None:
        # span=400 -> r=1.0; osi=-10 -> -((1-(-10))/1)/100=-0.11
        assert core.level_count_score(-10.0, 400.0, 3) == pytest.approx(-0.11)


# ---------------------------------------------------------------------------
# 観点 8: 退化ゼロ割（span==50 で inf or nan・ガードしない）
# ---------------------------------------------------------------------------
class TestLevelCountScoreDegenerate:
    def test_span_50_yields_inf_or_nan_not_exception(self) -> None:
        # Arrange: span==50 -> r=0 -> ((osi-50)/0) ゼロ割。ガードしないため inf/nan を許容。
        with np.errstate(divide="ignore", invalid="ignore"):
            # Act
            v = core.level_count_score(70.0, 50.0, 0)
        # Assert: 例外を投げず inf or nan（1:1 再現）。
        assert np.isinf(v) or np.isnan(v)

    def test_span_0_case2_yields_nan(self) -> None:
        # Arrange: span==0 -> r=0、osi=0 -> (0-0)/0 = nan。
        with np.errstate(divide="ignore", invalid="ignore"):
            v = core.level_count_score(0.0, 0.0, 2)
        assert np.isnan(v)


# ---------------------------------------------------------------------------
# 観点 6: 合算（4 採点の和＝level_count・warm-up 寄与込み）
# ---------------------------------------------------------------------------
class TestComputeRmmAggregation:
    def _make_data(self) -> tuple[np.ndarray, ...]:
        rng = np.random.default_rng(3)
        high = np.cumsum(rng.random(30)) + 10.0
        low = high - rng.random(30) - 0.5
        close = low + rng.random(30) * (high - low)
        volume = rng.integers(1, 100, 30).astype(float)
        return high, low, close, volume

    def test_level_count_equals_sum_of_four_scores_per_bar(self) -> None:
        # Arrange
        high, low, close, volume = self._make_data()
        osc_period, ma_period = 6, 6
        # Act
        result = core.compute_rmm(
            high, low, close, volume, osc_period=osc_period, ma_period=ma_period
        )
        # 期待値を独立に再構成する（採点ロジックを test 側で 1:1 計算）。
        from common import typical_price  # noqa: E402

        typical = typical_price(high, low, close)
        rsi = result.rsi
        mfi = result.mfi
        wpr = result.wpr  # +100 済み
        marod = result.marod
        rsi_span = core.oscillator_span(rsi, clamp=True)
        wpr_span = core.oscillator_span(wpr, clamp=True)
        mfi_span = core.oscillator_span(mfi, clamp=True)
        marod_span = core.oscillator_span(marod, clamp=False)
        expected = np.zeros(len(close))
        for i in range(len(close)):
            lc = 0.0
            if rsi[i] < 50:
                lc += core.level_count_score(rsi[i], rsi_span, 1)
            elif rsi[i] > 50:
                lc += core.level_count_score(rsi[i], rsi_span, 0)
            if wpr[i] < 50:
                lc += core.level_count_score(wpr[i], wpr_span, 1)
            elif wpr[i] > 50:
                lc += core.level_count_score(wpr[i], wpr_span, 0)
            if mfi[i] < 50:
                lc += core.level_count_score(mfi[i], mfi_span, 1)
            elif mfi[i] > 50:
                lc += core.level_count_score(mfi[i], mfi_span, 0)
            if marod[i] < 0:
                lc += core.level_count_score(marod[i], marod_span, 2)
            elif marod[i] > 0:
                lc += core.level_count_score(marod[i], marod_span, 3)
            expected[i] = lc
        # Assert
        np.testing.assert_allclose(result.level_count, expected)

    def test_wpr_buffer_is_raw_plus_100(self) -> None:
        # Arrange: result.wpr は生 WPR + 100.0（warm-up=0→+100=100→case0 になる）。
        high, low, close, volume = self._make_data()
        # Act
        result = core.compute_rmm(high, low, close, volume, osc_period=6, ma_period=6)
        raw = core.compute_wpr(high, low, close, period=6)
        # Assert
        np.testing.assert_allclose(result.wpr, raw + 100.0)


# ---------------------------------------------------------------------------
# 観点 7: LC の σ6 水準（母σ÷N・6 本）
# ---------------------------------------------------------------------------
class TestComputeRmmLevels:
    def test_six_levels_with_population_std(self) -> None:
        # Arrange
        level_count = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        avg = float(np.mean(level_count))
        dev = float(np.sqrt(np.mean((level_count - avg) ** 2)))  # 母σ÷N
        # Act
        levels = core.compute_rmm_levels(level_count)
        # Assert: 6 本・母σ（標本σ ddof=1 だと fail する discriminating）。
        assert set(levels.keys()) == {
            "up_1s", "up_2s", "up_3s", "dn_1s", "dn_2s", "dn_3s",
        }
        assert levels["up_1s"] == pytest.approx(avg + dev)
        assert levels["up_2s"] == pytest.approx(avg + 2 * dev)
        assert levels["up_3s"] == pytest.approx(avg + 3 * dev)
        assert levels["dn_1s"] == pytest.approx(avg - dev)
        assert levels["dn_2s"] == pytest.approx(avg - 2 * dev)
        assert levels["dn_3s"] == pytest.approx(avg - 3 * dev)

    def test_population_std_differs_from_sample_std(self) -> None:
        # Arrange: 標本σ（ddof=1）で実装すると fail する discriminating 入力。
        level_count = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        avg = float(np.mean(level_count))
        sample_dev = float(np.std(level_count, ddof=1))
        # Act
        levels = core.compute_rmm_levels(level_count)
        # Assert: up_1s が標本σ版と一致しないこと（母σであることの固定）。
        assert levels["up_1s"] != pytest.approx(avg + sample_dev)


# ---------------------------------------------------------------------------
# 観点 9: 例外（osc_period<2→ValueError・長不一致→ValueError）
# ---------------------------------------------------------------------------
class TestComputeRmmExceptions:
    def test_osc_period_below_2_raises_value_error(self) -> None:
        high = np.array([10.0, 11.0, 12.0])
        low = np.array([8.0, 9.0, 10.0])
        close = np.array([9.0, 10.0, 11.0])
        volume = np.array([1.0, 2.0, 3.0])
        with pytest.raises(ValueError):
            core.compute_rmm(high, low, close, volume, osc_period=1)

    def test_length_mismatch_raises_value_error(self) -> None:
        high = np.array([10.0, 11.0, 12.0])
        low = np.array([8.0, 9.0])  # 長さ不一致
        close = np.array([9.0, 10.0, 11.0])
        volume = np.array([1.0, 2.0, 3.0])
        with pytest.raises(ValueError):
            core.compute_rmm(high, low, close, volume, osc_period=6)


# ---------------------------------------------------------------------------
# 観点 10: DTO 不変性（全 ndarray writeable=False・frozen）
# ---------------------------------------------------------------------------
class TestRmmResultImmutability:
    def _result(self) -> "core.RmmResult":
        rng = np.random.default_rng(4)
        high = np.cumsum(rng.random(30)) + 10.0
        low = high - rng.random(30) - 0.5
        close = low + rng.random(30) * (high - low)
        volume = rng.integers(1, 100, 30).astype(float)
        return core.compute_rmm(high, low, close, volume, osc_period=6, ma_period=6)

    def test_all_ndarrays_are_not_writeable(self) -> None:
        result = self._result()
        for name in ("level_count", "rsi", "wpr", "mfi", "marod"):
            arr = getattr(result, name)
            assert arr.flags.writeable is False, f"{name} must be writeable=False"

    def test_dto_is_frozen(self) -> None:
        result = self._result()
        with pytest.raises(Exception):
            result.level_count = np.zeros(3)  # type: ignore[misc]

    def test_lc_levels_present(self) -> None:
        result = self._result()
        assert set(result.lc_levels.keys()) == {
            "up_1s", "up_2s", "up_3s", "dn_1s", "dn_2s", "dn_3s",
        }
