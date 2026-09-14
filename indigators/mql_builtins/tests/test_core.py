"""mql_builtins 共有プリミティブの検証。

compute_rsi / compute_mfi / compute_wpr / compute_stochastic を手計算可能な
小入力で固定する（正準実装の挙動を本ライブラリの独立な手計算で担保する。各消費者
パッケージとの一致比較は集約により同一コードの再公開＝トートロジー化したため削除済み）。
併せて ``period`` がキーワード必須（位置引数渡し不可）であることを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# src（mql_builtins 配下）。テスト規約: from src import を不変に保つ。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    compute_mfi,
    compute_rsi,
    compute_stochastic,
    compute_wpr,
)


# =========================================================== compute_rsi
class TestComputeRsi:
    def test_warmup_is_zero_and_len_le_period_all_zero(self) -> None:
        # Arrange: len(price) <= period のときは全 0（元 RSI.mq5 早期 return）。
        price = np.array([1.0, 2.0, 3.0])
        # Act
        got = compute_rsi(price, period=3)
        # Assert
        np.testing.assert_array_equal(got, np.zeros(3))

    def test_flat_window_returns_50(self) -> None:
        # Arrange: 完全フラット（pos==0 かつ neg==0）→ 50。
        price = np.array([5.0, 5.0, 5.0, 5.0, 5.0])
        # Act
        got = compute_rsi(price, period=2)
        # Assert: warm-up [0,1]=0、以降は 50。
        assert got[0] == 0.0 and got[1] == 0.0
        np.testing.assert_array_equal(got[2:], np.array([50.0, 50.0, 50.0]))

    def test_all_up_window_returns_100(self) -> None:
        # Arrange: 単調増加（neg==0, pos!=0）→ 100。
        price = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        # Act
        got = compute_rsi(price, period=2)
        # Assert
        np.testing.assert_array_equal(got[2:], np.array([100.0, 100.0, 100.0]))

    def test_period_below_2_raises(self) -> None:
        # Arrange / Act / Assert: period<2 は ValueError。
        with pytest.raises(ValueError):
            compute_rsi(np.array([1.0, 2.0, 3.0]), period=1)

    def test_period_is_keyword_only(self) -> None:
        # Arrange / Act / Assert: period の位置引数渡しは不可（契約: キーワード必須）。
        with pytest.raises(TypeError):
            compute_rsi(np.array([1.0, 2.0, 3.0]), 2)  # type: ignore[misc]


# =========================================================== compute_mfi
class TestComputeMfi:
    def test_warmup_is_zero(self) -> None:
        # Arrange
        high = np.array([2.0, 3.0, 4.0, 5.0])
        low = np.array([1.0, 2.0, 3.0, 4.0])
        close = np.array([1.5, 2.5, 3.5, 4.5])
        volume = np.array([10.0, 10.0, 10.0, 10.0])
        # Act
        got = compute_mfi(high, low, close, volume, period=2)
        # Assert: warm-up [0,1]=0。
        assert got[0] == 0.0 and got[1] == 0.0

    def test_all_up_window_returns_100(self) -> None:
        # Arrange: TP 単調増加（負MF==0）→ 100。
        high = np.array([2.0, 3.0, 4.0, 5.0])
        low = np.array([1.0, 2.0, 3.0, 4.0])
        close = np.array([1.5, 2.5, 3.5, 4.5])
        volume = np.array([10.0, 20.0, 30.0, 40.0])
        # Act
        got = compute_mfi(high, low, close, volume, period=2)
        # Assert
        np.testing.assert_array_equal(got[2:], np.array([100.0, 100.0]))

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_mfi(
                np.array([1.0, 2.0]), np.array([1.0]),
                np.array([1.0, 2.0]), np.array([1.0, 2.0]), period=2,
            )

    def test_period_is_keyword_only(self) -> None:
        a = np.array([1.0, 2.0, 3.0])
        with pytest.raises(TypeError):
            compute_mfi(a, a, a, a, 2)  # type: ignore[misc]


# =========================================================== compute_wpr
class TestComputeWpr:
    def test_warmup_is_i_below_period_minus_1(self) -> None:
        # Arrange: WPR の warm-up は i<period-1（iRSI/iMFI より 1 本ズレる）。
        high = np.array([2.0, 3.0, 4.0, 5.0])
        low = np.array([1.0, 2.0, 3.0, 4.0])
        close = np.array([1.5, 2.5, 3.5, 4.5])
        # Act
        got = compute_wpr(high, low, close, period=3)
        # Assert: i=0,1 が 0、最初の有効値は i=period-1=2。
        assert got[0] == 0.0 and got[1] == 0.0
        assert got[2] != 0.0

    def test_handcalc_close_at_high(self) -> None:
        # Arrange: 終値が窓内最高値 → -(maxH-close)*100/(maxH-minL)=0。
        high = np.array([10.0, 10.0])
        low = np.array([0.0, 0.0])
        close = np.array([10.0, 10.0])
        # Act
        got = compute_wpr(high, low, close, period=2)
        # Assert: i=1 で close==maxH → 0。
        assert got[1] == 0.0

    def test_flat_window_carries_previous(self) -> None:
        # Arrange: maxH==minL のとき前値を引き継ぐ。
        high = np.array([5.0, 5.0, 5.0])
        low = np.array([5.0, 5.0, 5.0])
        close = np.array([5.0, 5.0, 5.0])
        # Act
        got = compute_wpr(high, low, close, period=2)
        # Assert: 全フラット → 全 0（前値 0 を引き継ぐ）。
        np.testing.assert_array_equal(got, np.zeros(3))

    def test_period_is_keyword_only(self) -> None:
        a = np.array([1.0, 2.0, 3.0])
        with pytest.raises(TypeError):
            compute_wpr(a, a, a, 2)  # type: ignore[misc]


# =========================================================== compute_stochastic
class TestComputeStochastic:
    def test_warmup_is_zero(self) -> None:
        # Arrange
        high = np.array([2.0, 3.0, 4.0, 5.0])
        low = np.array([1.0, 2.0, 3.0, 4.0])
        close = np.array([1.5, 2.5, 3.5, 4.5])
        # Act
        got = compute_stochastic(high, low, close, period=3)
        # Assert: i<period-1=2 は 0。
        assert got[0] == 0.0 and got[1] == 0.0

    def test_handcalc_percent_k(self) -> None:
        # Arrange: 窓 [HH=4, LL=1], close=2 → 100*(2-1)/(4-1)=33.333...
        high = np.array([3.0, 4.0])
        low = np.array([1.0, 2.0])
        close = np.array([2.0, 2.0])
        # Act
        got = compute_stochastic(high, low, close, period=2)
        # Assert
        assert got[1] == pytest.approx(100.0 * (2.0 - 1.0) / (4.0 - 1.0))

    def test_zero_range_returns_zero(self) -> None:
        # Arrange: HH==LL → 0（ゼロ割ガード）。
        high = np.array([5.0, 5.0])
        low = np.array([5.0, 5.0])
        close = np.array([5.0, 5.0])
        # Act
        got = compute_stochastic(high, low, close, period=2)
        # Assert
        assert got[1] == 0.0

    def test_period_below_2_raises(self) -> None:
        a = np.array([1.0, 2.0, 3.0])
        with pytest.raises(ValueError):
            compute_stochastic(a, a, a, period=1)

    def test_period_is_keyword_only(self) -> None:
        a = np.array([1.0, 2.0, 3.0])
        with pytest.raises(TypeError):
            compute_stochastic(a, a, a, 2)  # type: ignore[misc]
