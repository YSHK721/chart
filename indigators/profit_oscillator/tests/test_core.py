"""PRO!fit_Oscillator コア計算の検証（元 MQL4 ``PRO!fit_Oscillator.mq4`` + PS.mqh を 1:1 固定）。

手計算可能な小入力で iRVI（権威 RVI.mq5 の三角加重・period 窓和・sum_down==0→sum_up・
warm-up i<period+2→0）・iMARD（対称 6 価格 / WEIGHTED 非対称 / ma==0→0）・iStochastic
2 モード一致（生 %K）・複製一致（compute_rsi / compute_mfi / compute_stochastic /
ps_level_count / compute_sigma_levels が複製元と数値一致）・18 系列集計順序・σ12 水準・
±3.29σ クランプ・DTO 不変性・例外を discriminating に固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# src（profit_oscillator 配下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 複製元（profit_arctan / profit_rsi / profit_mfi / profit_stc）と突き合わせるため indicators を path に追加
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import core  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root → common
from common import AppliedPrice, applied_price  # noqa: E402

# 複製元モジュール（数値一致の検証用）。各 core.py をファイルパスから一意名でロードし、
# パッケージ ``src`` 名前空間の衝突（複数 indicator が src/core を持つ）を避ける。
import importlib.util  # noqa: E402

_INDICATORS = Path(__file__).resolve().parents[2]


def _load_core(pkg: str):
    name = f"{pkg}_core_ref"
    path = _INDICATORS / pkg / "src" / "core.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass の __module__ 解決のため事前登録
    spec.loader.exec_module(mod)
    return mod


arctan_core = _load_core("profit_arctan")
rsi_core = _load_core("profit_rsi")
mfi_core = _load_core("profit_mfi")
stc_core = _load_core("profit_stc")


# ==================================================================== iRVI（権威 RVI.mq5）
class TestComputeRvi:
    """iRVI を権威 RVI.mq5（三角加重 1,2,2,1・period 窓和・sum_down==0→sum_up・warm-up）で固定する。"""

    def _series(self):
        o = np.array([1.0, 1, 1, 1, 1, 1, 1, 1])
        c = np.array([2.0, 3, 2, 4, 5, 3, 6, 2])  # c-o = [1,2,1,3,4,2,5,1]
        h = np.array([3.0, 3, 3, 3, 3, 3, 3, 3])
        l = np.array([1.0, 1, 1, 1, 1, 1, 1, 1])  # h-l = 2 (定数)
        return o, h, l, c

    def test_rvi_period2_matches_authoritative_triangle_weighted_window_sum(self):
        # Arrange: 手計算で value_up を三角加重し period=2 窓和したもの（§確定セマンティクス 1）。
        o, h, l, c = self._series()
        # Act
        rvi = core.compute_rvi(o, h, l, c, period=2)
        # Assert: i=5 -> 31/24, i=6 -> 37/24, i=7 -> 39/24（sum_down=24 一定）
        assert rvi[5] == pytest.approx(31.0 / 24.0)
        assert rvi[6] == pytest.approx(37.0 / 24.0)
        assert rvi[7] == pytest.approx(39.0 / 24.0)

    def test_rvi_warmup_below_period_plus_two_is_zero(self):
        # Arrange: period=2 -> warm-up は i<4（period+2）まで 0。最初の実値は i=period+2=4。
        # 権威 RVI.mq5 の計算開始 start=period+2（main ループが index period+2 を上書き）。
        o, h, l, c = self._series()
        # Act
        rvi = core.compute_rvi(o, h, l, c, period=2)
        # Assert
        assert np.all(rvi[:4] == 0.0)
        assert rvi[4] == pytest.approx(24.0 / 24.0)  # i=period+2 は非 0（窓[3,4]: 24/24=1.0）

    def test_rvi_sum_down_zero_returns_sum_up(self):
        # Arrange: high==low の系列で sum_down==0 -> RVI=sum_up（§確定セマンティクス 1）。
        o = np.array([1.0, 1, 1, 1, 1, 1, 1, 1])
        c = np.array([2.0, 3, 2, 4, 5, 3, 6, 2])  # c-o = [1,2,1,3,4,2,5,1]
        flat = np.ones(8)  # high==low -> h-l=0
        # Act
        rvi = core.compute_rvi(o, flat, flat, c, period=2)
        # Assert: i=5 の sum_up = vu(4)+vu(5) = 14+17 = 31
        assert rvi[5] == pytest.approx(31.0)


# ==================================================================== iMARD（iMARD・EMA 固定）
class TestComputeMard:
    """iMARD を対称 6 価格 / WEIGHTED 非対称 / ma==0→0 で discriminating に固定する。"""

    def _series(self):
        o = np.array([1.0, 2, 3, 4, 5])
        h = np.array([2.0, 3, 4, 5, 6])
        l = np.array([0.5, 1.5, 2.5, 3.5, 4.5])
        c = np.array([1.5, 2.5, 3.5, 4.5, 5.5])
        return o, h, l, c

    def test_mard_close_symmetric_equals_price_minus_ema_over_ema(self):
        # Arrange: CLOSE（対称）。num=denom=close, res=(close-EMA(close))/EMA(close)。
        from moving_averages import exponential_ma_on_buffer

        o, h, l, c = self._series()
        price = applied_price(AppliedPrice.CLOSE, o, h, l, c)
        ma = np.zeros(price.size)
        exponential_ma_on_buffer(price.size, 0, 0, 2, price, ma)
        expected = np.where(ma == 0.0, 0.0, (price - ma) / ma)
        # Act
        res = core.compute_mard(o, h, l, c, period=2, applied=AppliedPrice.CLOSE)
        # Assert
        np.testing.assert_allclose(res, expected)
        # discriminating な具体値（index 1）
        assert res[1] == pytest.approx(0.15384615384615394)

    def test_mard_weighted_is_asymmetric_numerator_ohlc_avg(self):
        # Arrange: WEIGHTED は分子 (O+H+L+C)/4・分母 EMA((H+L+2C)/4) の非対称（§確定セマンティクス 2）。
        o, h, l, c = self._series()
        # Act
        res = core.compute_mard(o, h, l, c, period=2, applied=AppliedPrice.WEIGHTED)
        # Assert: 正しい非対称値 res[1]=0.10204。対称（誤）実装なら 0.16327 になり弾かれる。
        assert res[1] == pytest.approx(0.1020408163265307)
        assert res[1] != pytest.approx(0.16326530612244908)  # 対称実装の誤値を排除

    def test_mard_ma_zero_returns_zero(self):
        # Arrange: 全 0 系列 -> EMA も 0 -> 退化ガードで res=0（§確定セマンティクス 2）。
        z = np.zeros(5)
        # Act
        res = core.compute_mard(z, z, z, z, period=2, applied=AppliedPrice.CLOSE)
        # Assert
        assert np.all(res == 0.0)


# ==================================================== iStochastic 2 モード一致（生 %K）
class TestStochasticTwoModes:
    """IC02 の main / signal が生 %K（同一配列）に帰着することを固定する（元 L177-178）。"""

    def test_stochastic_main_and_signal_are_identical_raw_k(self):
        # Arrange
        h = np.array([2.0, 3, 4, 5, 6, 5, 4])
        l = np.array([1.0, 1, 2, 3, 3, 2, 1])
        c = np.array([1.5, 2.5, 3.5, 4.5, 5.0, 3.0, 2.0])
        # Act: 18 系列集計に渡るのは compute_stochastic（生 %K）を 2 回。
        k = core.compute_stochastic(h, l, c, period=3)
        # Assert: 複製元 STC の %K と一致（main=signal=生 %K）。
        np.testing.assert_allclose(k, stc_core.compute_stochastic(h, l, c, period=3))


# ==================================================== 複製一致（複製元と数値一致）
class TestVerbatimCopies:
    """compute_rsi / compute_mfi / compute_stochastic / ps_level_count / compute_sigma_levels が複製元と一致。"""

    def test_compute_rsi_matches_profit_rsi(self):
        price = np.array([1.0, 2, 1.5, 3, 2.5, 4, 3.5, 5, 2, 6])
        np.testing.assert_allclose(
            core.compute_rsi(price, period=3),
            rsi_core.compute_rsi(price, period=3),
        )

    def test_compute_mfi_matches_profit_mfi(self):
        h = np.array([2.0, 3, 4, 5, 6, 5, 4, 7])
        l = np.array([1.0, 1, 2, 3, 3, 2, 1, 4])
        c = np.array([1.5, 2.5, 3.5, 4.5, 5.0, 3.0, 2.0, 6.0])
        v = np.array([10.0, 12, 9, 15, 8, 11, 7, 20])
        np.testing.assert_allclose(
            core.compute_mfi(h, l, c, v, period=3),
            mfi_core.compute_mfi(h, l, c, v, period=3),
        )

    def test_compute_stochastic_matches_profit_stc(self):
        h = np.array([2.0, 3, 4, 5, 6, 5, 4])
        l = np.array([1.0, 1, 2, 3, 3, 2, 1])
        c = np.array([1.5, 2.5, 3.5, 4.5, 5.0, 3.0, 2.0])
        np.testing.assert_allclose(
            core.compute_stochastic(h, l, c, period=3),
            stc_core.compute_stochastic(h, l, c, period=3),
        )

    def test_ps_level_count_matches_profit_arctan(self):
        # 共有層 profit_system の ps_level_count と一致（同一実装を参照していること）。
        from profit_system import ps_level_count as ref_plc  # noqa: E402

        arr = np.array([0.1, 0.5, -0.3, 0.8, -0.2, 0.4, 0.0, 0.9])
        np.testing.assert_allclose(
            core.ps_level_count(arr, None, initialization=True),
            ref_plc(arr, None, initialization=True),
        )

    def test_compute_sigma_levels_matches_profit_arctan(self):
        from profit_system import compute_sigma_levels as ref_csl  # noqa: E402

        lc = np.array([0.1, 0.5, -0.3, 0.8, -0.2, 0.4, 0.0, 0.9, 1.2, -0.7])
        got = core.compute_sigma_levels(lc)
        exp = ref_csl(lc)
        assert set(got.keys()) == set(exp.keys())
        for k in exp:
            assert got[k] == pytest.approx(exp[k])


# ==================================================== 18 系列集計順序・本数
class TestLevelCountAggregation:
    """compute_level_count が 18 系列を順序厳守で加算し IC01_W のみ initialization=True であることを固定。"""

    def _ohlcv(self, n=40):
        rng = np.random.default_rng(7)
        base = np.cumsum(rng.normal(0, 1, n)) + 100.0
        o = base + rng.normal(0, 0.1, n)
        c = base + rng.normal(0, 0.1, n)
        h = np.maximum(o, c) + np.abs(rng.normal(0, 0.2, n))
        l = np.minimum(o, c) - np.abs(rng.normal(0, 0.2, n))
        v = np.abs(rng.normal(1000, 100, n))
        return o, h, l, c, v

    def test_level_count_equals_sum_of_18_series_in_order(self):
        # Arrange: 18 系列を仕様順に手で構築し、ps_level_count を逐次加算したものと一致させる。
        o, h, l, c, v = self._ohlcv()
        pa, pb = 6, 60
        order = (
            AppliedPrice.WEIGHTED,
            AppliedPrice.TYPICAL,
            AppliedPrice.MEDIAN,
            AppliedPrice.HIGH,
            AppliedPrice.LOW,
            AppliedPrice.OPEN,
            AppliedPrice.CLOSE,
        )
        series_list = []
        # IC01 RSI (7)
        for k in order:
            series_list.append(core.compute_rsi(applied_price(k, o, h, l, c), period=pa))
        # IC02 Stochastic main / signal (2) -> 同一生 %K
        st = core.compute_stochastic(h, l, c, period=pa)
        series_list.append(st)
        series_list.append(st)
        # IC03 MFI (1)
        series_list.append(core.compute_mfi(h, l, c, v, period=pa))
        # IC04 RVI (1)
        series_list.append(core.compute_rvi(o, h, l, c, period=pa))
        # IC05 MARD (7)
        for k in order:
            series_list.append(core.compute_mard(o, h, l, c, period=pb, applied=k))

        assert len(series_list) == 18
        lc = None
        for idx, s in enumerate(series_list):
            lc = core.ps_level_count(s, lc, initialization=(idx == 0))

        # Act（全期間版＝参照 ps_level_count と同じ基準で 18 系列加算順序を固定）
        got = core.compute_level_count(o, h, l, c, v, period_a=pa, period_b=pb, window=None)
        # Assert
        np.testing.assert_allclose(got, lc)


# ==================================================== σ12 + クランプ + DTO
class TestOscillatorFull:
    """compute_oscillator_full が σ12・±3.29σ クランプ・frozen DTO を満たすことを固定する。"""

    def _ohlcv(self, n=50):
        rng = np.random.default_rng(11)
        base = np.cumsum(rng.normal(0, 1, n)) + 100.0
        o = base + rng.normal(0, 0.1, n)
        c = base + rng.normal(0, 0.1, n)
        h = np.maximum(o, c) + np.abs(rng.normal(0, 0.2, n))
        l = np.minimum(o, c) - np.abs(rng.normal(0, 0.2, n))
        v = np.abs(rng.normal(1000, 100, n))
        return o, h, l, c, v

    def test_levels_equals_sigma_levels_of_raw(self):
        o, h, l, c, v = self._ohlcv()
        res = core.compute_oscillator_full(o, h, l, c, v)
        np.testing.assert_allclose(
            list(res.levels.values()),
            list(core.compute_oscillator_levels(res.raw_level_count).values()),
        )

    def test_clamped_within_dn329_up329(self):
        o, h, l, c, v = self._ohlcv()
        res = core.compute_oscillator_full(o, h, l, c, v, window=None)  # 全期間版でクランプを固定
        up = res.levels["up_329"]
        dn = res.levels["dn_329"]
        assert np.all(res.level_count_clamped <= up + 1e-12)
        assert np.all(res.level_count_clamped >= dn - 1e-12)
        expected = np.clip(res.raw_level_count, dn, up)
        np.testing.assert_allclose(res.level_count_clamped, expected)


    def test_causal_warmup_nan_and_no_repaint(self):
        o, h, l, c, v = self._ohlcv(n=400)
        W, bar = 120, 250
        res = core.compute_oscillator_full(o, h, l, c, v, window=W)
        assert np.all(np.isnan(res.raw_level_count[:W - 1]))
        assert np.isfinite(res.raw_level_count[bar])
        short = core.compute_oscillator_full(
            o[:300], h[:300], l[:300], c[:300], v[:300], window=W
        )
        np.testing.assert_allclose(
            res.raw_level_count[bar], short.raw_level_count[bar], rtol=1e-12, atol=1e-12
        )

    def test_dto_is_frozen_and_arrays_not_writeable(self):
        o, h, l, c, v = self._ohlcv()
        res = core.compute_oscillator_full(o, h, l, c, v)
        assert not res.level_count_clamped.flags.writeable
        assert not res.raw_level_count.flags.writeable
        with pytest.raises((AttributeError, Exception)):
            res.raw_level_count = np.zeros(3)  # frozen DTO

    def test_oscillator_levels_alias_equals_sigma_levels(self):
        lc = np.array([0.1, 0.5, -0.3, 0.8, -0.2, 0.4, 0.0, 0.9, 1.2, -0.7])
        assert core.compute_oscillator_levels(lc) == core.compute_sigma_levels(lc)


# ==================================================== 例外
class TestExceptions:
    def test_period_a_below_two_raises(self):
        z = np.ones(10)
        with pytest.raises(ValueError):
            core.compute_oscillator_full(z, z, z, z, z, period_a=1)

    def test_period_b_below_two_raises(self):
        z = np.ones(10)
        with pytest.raises(ValueError):
            core.compute_oscillator_full(z, z, z, z, z, period_b=1)

    def test_ohlcv_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            core.compute_oscillator_full(
                np.ones(10), np.ones(10), np.ones(10), np.ones(10), np.ones(9)
            )
