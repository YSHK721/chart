"""PRO!fitRMMMACD core 層（純粋計算）の検証。

元 MQL4 ``PRO!fitRMMMACD.mq4``（RMM レベルカウント＋MACD連鎖の変種）を昇順
（古→新, index 0=最古）へ 1:1 変換した一意定義を固定する。本指標は profit_rmm の
level_count（4 オシレーター funLevelCount 合算）を再利用し、その level_count に
MACD 連鎖を適用する。**ただし MFIMACD/RSIMACD とは 2 点が異なる**:

    重要差分①: macd[i] = slow[i] - fast[i]（MFIMACD の fast-slow と逆。元 L272）
    重要差分②: histogram[i] = macd[i] - signal[i]（×2.618 係数なし。元 L280）

固定する discriminating 観点（依頼仕様 §TDD 1..7）::

    1. level_count が profit_rmm の level_count と完全一致（複製の同一性）
    2. macd = slow - fast（fast-slow と逆。符号逆なら fail させる discriminating）
    3. histogram = macd - signal（係数 2.618 を掛けない discriminating）
    4. EMA 連鎖（fast/slow が共有 exponential_ma_on_buffer と一致・signal=EMA(macd,4)）
    5. σ 水準が無い（RmmMacdResult に levels フィールドが無い）を構造で担保
    6. 例外（osc_period<2→ValueError・HLCV 長不一致→ValueError）
    7. DTO 不変性（全 ndarray writeable=False・frozen）
"""

import dataclasses
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = profit_rmm_macd/

from src import core  # noqa: E402

# profit_rmm の正準 level_count を取り込む。``src`` パッケージ名が本パッケージと衝突
# する（pytest 横断 sys.modules 汚染）ため、正準 core.py をファイル指定で別名ロードする。
_rmm_core_path = (
    Path(__file__).resolve().parents[2] / "profit_rmm" / "src" / "core.py"
)
_spec = importlib.util.spec_from_file_location("profit_rmm_core", _rmm_core_path)
rmm_core = importlib.util.module_from_spec(_spec)
sys.modules["profit_rmm_core"] = rmm_core  # dataclass の注釈解決に必要
_spec.loader.exec_module(rmm_core)

# 共有 EMA（fast/slow/signal の連鎖一致検証用）。
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)  # = indicators/
from moving_averages import exponential_ma_on_buffer  # noqa: E402


# ---------------------------------------------------------------------------
# テスト用 OHLCV フィクスチャ（warm-up を超える長さ・値の変化があるもの）
# ---------------------------------------------------------------------------
def _sample_ohlcv() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """昇順 OHLCV（長さ 20）。レベルカウントと EMA 連鎖を意味のある値にする。"""
    high = np.array(
        [10, 12, 11, 13, 9, 14, 15, 13, 16, 12,
         17, 18, 16, 19, 15, 20, 21, 19, 22, 18],
        dtype=np.float64,
    )
    low = np.array(
        [8, 9, 7, 10, 6, 11, 12, 10, 13, 9,
         14, 15, 13, 16, 12, 17, 18, 16, 19, 15],
        dtype=np.float64,
    )
    close = np.array(
        [9, 11, 10, 12, 8, 13, 14, 11, 15, 10,
         16, 17, 14, 18, 13, 19, 20, 17, 21, 16],
        dtype=np.float64,
    )
    volume = np.array(
        [100, 120, 110, 130, 90, 140, 150, 130, 160, 120,
         170, 180, 160, 190, 150, 200, 210, 190, 220, 180],
        dtype=np.float64,
    )
    return high, low, close, volume


# ===========================================================================
# 観点 1: level_count が profit_rmm の level_count と完全一致
# ===========================================================================
class TestComputeRmmLevelCount:
    def test_level_count_matches_profit_rmm_level_count_for_default_params(
        self,
    ) -> None:
        # Arrange: 同一入力・同一既定 osc/ma 期間。
        high, low, close, volume = _sample_ohlcv()
        # Act
        got = core.compute_rmm_level_count(high, low, close, volume)
        expected = rmm_core.compute_rmm(high, low, close, volume).level_count
        # Assert: 複製パイプラインの同一性（bit-for-bit）。
        np.testing.assert_allclose(got, expected, rtol=0, atol=0)

    def test_level_count_matches_profit_rmm_for_custom_periods(self) -> None:
        # Arrange
        high, low, close, volume = _sample_ohlcv()
        # Act
        got = core.compute_rmm_level_count(
            high, low, close, volume, osc_period=5, ma_period=4
        )
        expected = rmm_core.compute_rmm(
            high, low, close, volume, osc_period=5, ma_period=4
        ).level_count
        # Assert
        np.testing.assert_allclose(got, expected, rtol=0, atol=0)


# ===========================================================================
# 観点 2: macd = slow - fast（fast-slow と逆。discriminating）
# ===========================================================================
class TestMacdIsSlowMinusFast:
    def test_macd_equals_slow_minus_fast_not_fast_minus_slow(self) -> None:
        # Arrange: fast(4) != slow(8) なので fast-slow と slow-fast は別物。
        high, low, close, volume = _sample_ohlcv()
        # Act
        result = core.compute_rmmmacd(high, low, close, volume)
        # Assert: macd == slow-fast（重要差分①）。
        np.testing.assert_allclose(
            result.macd, result.slow - result.fast, rtol=0, atol=0
        )
        # discriminating: fast-slow（MFIMACD 形）と一致してはならない。
        assert not np.allclose(result.macd, result.fast - result.slow)


# ===========================================================================
# 観点 3: histogram = macd - signal（係数 2.618 なし。discriminating）
# ===========================================================================
class TestHistogramHasNoCoefficient:
    def test_histogram_equals_macd_minus_signal_without_coefficient(self) -> None:
        # Arrange
        high, low, close, volume = _sample_ohlcv()
        # Act
        result = core.compute_rmmmacd(high, low, close, volume)
        # Assert: histogram == macd-signal（重要差分②・係数なし）。
        np.testing.assert_allclose(
            result.histogram, result.macd - result.signal, rtol=0, atol=0
        )
        # discriminating: 2.618 係数版（MFIMACD 形）と一致してはならない。
        assert not np.allclose(
            result.histogram, 2.618 * (result.macd - result.signal)
        )


# ===========================================================================
# 観点 4: EMA 連鎖（共有 exponential_ma_on_buffer と一致）
# ===========================================================================
class TestEmaChain:
    def test_fast_slow_match_shared_ema_on_level_count(self) -> None:
        # Arrange: level_count を共有 EMA に通した期待値。
        high, low, close, volume = _sample_ohlcv()
        lc = core.compute_rmm_level_count(high, low, close, volume)
        n = lc.shape[0]
        exp_fast = np.zeros(n, dtype=np.float64)
        exponential_ma_on_buffer(n, 0, 0, 4, lc, exp_fast)
        exp_slow = np.zeros(n, dtype=np.float64)
        exponential_ma_on_buffer(n, 0, 0, 8, lc, exp_slow)
        # Act
        result = core.compute_rmmmacd(high, low, close, volume)
        # Assert
        np.testing.assert_allclose(result.fast, exp_fast, rtol=0, atol=0)
        np.testing.assert_allclose(result.slow, exp_slow, rtol=0, atol=0)

    def test_signal_equals_shared_ema_of_macd(self) -> None:
        # Arrange
        high, low, close, volume = _sample_ohlcv()
        result = core.compute_rmmmacd(high, low, close, volume)
        n = result.macd.shape[0]
        exp_signal = np.zeros(n, dtype=np.float64)
        exponential_ma_on_buffer(n, 0, 0, 4, result.macd, exp_signal)
        # Assert
        np.testing.assert_allclose(result.signal, exp_signal, rtol=0, atol=0)


# ===========================================================================
# 観点 5: σ 水準が無い（構造で担保）
# ===========================================================================
class TestNoSigmaLevels:
    def test_result_dto_has_no_levels_field(self) -> None:
        # Arrange/Act
        field_names = {f.name for f in dataclasses.fields(core.RmmMacdResult)}
        # Assert: levels / lc_levels を持たない。
        assert "levels" not in field_names
        assert "lc_levels" not in field_names

    def test_result_fields_are_exactly_the_six_chain_buffers(self) -> None:
        # Arrange/Act
        field_names = {f.name for f in dataclasses.fields(core.RmmMacdResult)}
        # Assert
        assert field_names == {
            "level_count",
            "fast",
            "slow",
            "macd",
            "signal",
            "histogram",
        }

    def test_core_module_has_no_levels_function(self) -> None:
        # Assert: σ 水準算出関数（compute_rmmmacd_levels 等）を公開しない。
        assert not hasattr(core, "compute_rmmmacd_levels")


# ===========================================================================
# 観点 6: 例外
# ===========================================================================
class TestExceptions:
    def test_osc_period_below_two_raises_value_error(self) -> None:
        high, low, close, volume = _sample_ohlcv()
        with pytest.raises(ValueError):
            core.compute_rmmmacd(high, low, close, volume, osc_period=1)

    def test_length_mismatch_raises_value_error(self) -> None:
        high, low, close, volume = _sample_ohlcv()
        with pytest.raises(ValueError):
            core.compute_rmmmacd(high, low, close, volume[:-1])

    def test_level_count_osc_period_below_two_raises_value_error(self) -> None:
        high, low, close, volume = _sample_ohlcv()
        with pytest.raises(ValueError):
            core.compute_rmm_level_count(high, low, close, volume, osc_period=1)

    def test_level_count_length_mismatch_raises_value_error(self) -> None:
        high, low, close, volume = _sample_ohlcv()
        with pytest.raises(ValueError):
            core.compute_rmm_level_count(high, low, close, volume[:-1])


# ===========================================================================
# 観点 7: DTO 不変性
# ===========================================================================
class TestDtoImmutability:
    def test_all_ndarray_fields_are_not_writeable(self) -> None:
        high, low, close, volume = _sample_ohlcv()
        result = core.compute_rmmmacd(high, low, close, volume)
        for name in ("level_count", "fast", "slow", "macd", "signal", "histogram"):
            arr = getattr(result, name)
            assert isinstance(arr, np.ndarray)
            assert arr.flags.writeable is False, name

    def test_dto_is_frozen(self) -> None:
        high, low, close, volume = _sample_ohlcv()
        result = core.compute_rmmmacd(high, low, close, volume)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.macd = np.zeros(1)  # type: ignore[misc]
