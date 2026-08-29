"""`common.marod_bands` の因果窓規約を固定する（ISSUE-449 T-3 で新設）。

本ファイルは :func:`common.marod_bands.rolling_causal_pointwise`（ISSUE-449 で加法追加した
点別版）の窓規約を固定する。既存関数（`rolling_causal` 等）の挙動は各指標パッケージの
テストが既に固定しており、本ファイルは**追加分のみ**を対象にする。

固定する核心: 点別版の窓規則が既存 :func:`rolling_causal` と**同一**であること
（水準到達シート §5.3 の帯内経験順位が、窓規則の第 2 定義を持たないことの機械的保証）。
"""
from __future__ import annotations

import numpy as np
import pytest

from common import marod_bands


def _window_sum(window: np.ndarray, current: float) -> float:
    """検査用 fn: 窓（当該バー除外・有限のみ）の総和。current は使わない。"""
    return float(window.sum())


def test_pointwise_window_excludes_the_current_bar() -> None:
    # Arrange: 窓が当該バーを含むなら t=2 は 1+2+3=6 になる。
    values = np.array([1.0, 2.0, 3.0, 4.0])

    # Act
    out = marod_bands.rolling_causal_pointwise(values, 10, _window_sum)

    # Assert: 窓 = values[max(0, t-n): t]（当該バー除外）。
    assert np.isnan(out[0])          # 窓 0 本
    assert np.isnan(out[1])          # 窓 1 本（MIN_STAT_OBS 未満）
    assert out[2] == pytest.approx(3.0)   # [1, 2]
    assert out[3] == pytest.approx(6.0)   # [1, 2, 3]


def test_pointwise_limits_the_window_to_window_n() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0])

    out = marod_bands.rolling_causal_pointwise(values, 2, _window_sum)

    assert out[3] == pytest.approx(5.0)   # values[1:3] = [2, 3]
    assert out[2] == pytest.approx(3.0)   # values[0:2] = [1, 2]


def test_pointwise_drops_non_finite_values_from_the_window() -> None:
    values = np.array([1.0, np.nan, 3.0, 4.0])

    out = marod_bands.rolling_causal_pointwise(values, 10, _window_sum)

    assert out[3] == pytest.approx(4.0)   # 有限のみ [1, 3]


def test_pointwise_returns_nan_when_the_current_value_is_not_finite() -> None:
    """当該バーが非有限なら fn を呼ばず NaN（fn が current を使うため既存版と規約が異なる）。"""
    values = np.array([1.0, 2.0, 3.0, np.nan])
    called: list[float] = []

    def spy(window: np.ndarray, current: float) -> float:
        called.append(current)
        return 0.0

    out = marod_bands.rolling_causal_pointwise(values, 10, spy)

    assert np.isnan(out[3])
    assert all(np.isfinite(c) for c in called)


def test_pointwise_returns_nan_when_the_finite_window_is_shorter_than_min_stat_obs() -> None:
    """境界値: 有限本数 == MIN_STAT_OBS で初めて値が出る。"""
    values = np.array([np.nan, 1.0, 2.0, 3.0])

    out = marod_bands.rolling_causal_pointwise(values, 10, _window_sum)

    assert np.isnan(out[1])                 # 有限窓 0 本
    assert np.isnan(out[2])                 # 有限窓 1 本 < MIN_STAT_OBS(2)
    assert out[3] == pytest.approx(3.0)     # 有限窓 2 本 == MIN_STAT_OBS


def test_pointwise_on_an_empty_series_returns_an_empty_array() -> None:
    out = marod_bands.rolling_causal_pointwise(np.array([]), 10, _window_sum)

    assert out.shape == (0,)


def test_pointwise_window_rule_is_identical_to_rolling_causal() -> None:
    """窓規則の第 2 定義を作っていないこと（ISSUE-449 §5.3 の単一ソース要件）。

    current を無視する fn を渡せば、既存 :func:`rolling_causal` と完全一致する。
    """
    # 決定的データ（乱数を使わない）: 周期の異なる 2 つの波の和。単調でも定数でもなく、
    # 窓ごとに平均が動くので「窓の切り出しが同一か」を区別できる。実行のたびに同じ列になる。
    index = np.arange(200, dtype=np.float64)
    values = np.sin(index * 0.7) + 0.5 * np.cos(index * 0.13)
    values[::17] = np.nan

    expected = marod_bands.rolling_causal(values, 30, lambda finite: float(finite.mean()))
    actual = marod_bands.rolling_causal_pointwise(
        values, 30, lambda window, _current: float(window.mean())
    )

    # rolling_causal は当該バーの有限性を見ないため、そこだけ差が出る（規約の差を明示）。
    only_current_nan = ~np.isfinite(values)
    np.testing.assert_allclose(actual[~only_current_nan], expected[~only_current_nan])


def test_pointwise_latest_equals_the_last_element_of_the_series_version() -> None:
    """末尾 1 点入口（ISSUE-449 レビュー 🔴-1）が系列版と**同一定義**であること。

    既存の 1 バーぶん入口（ISSUE-233）と対称の入口であり、窓規則の第 2 定義を作っていない
    ことの機械的保証。決定的データ（乱数を使わない）で全長・全窓幅を突き合わせる。
    """
    index = np.arange(120, dtype=np.float64)
    values = np.sin(index * 0.7) + 0.5 * np.cos(index * 0.13)
    values[::17] = np.nan

    for window_n in (2, 5, 30, 500):
        expected = marod_bands.rolling_causal_pointwise(values, window_n, _window_sum)
        actual = [
            marod_bands.causal_pointwise_latest(
                values[:t], values[t], window_n, _window_sum
            )
            for t in range(values.size)
        ]

        np.testing.assert_allclose(actual, expected, equal_nan=True)


def test_pointwise_latest_does_not_call_fn_when_the_window_is_too_short() -> None:
    """境界値: 有限窓 < MIN_STAT_OBS では fn を呼ばず NaN（無駄な発行を作らない）。"""
    called: list[float] = []

    def spy(window: np.ndarray, current: float) -> float:
        called.append(current)
        return 0.0

    out = marod_bands.causal_pointwise_latest(np.array([1.0]), 2.0, 10, spy)

    assert np.isnan(out)
    assert called == []


def test_pointwise_latest_returns_nan_when_the_current_value_is_not_finite() -> None:
    called: list[float] = []

    def spy(window: np.ndarray, current: float) -> float:
        called.append(current)
        return 0.0

    out = marod_bands.causal_pointwise_latest(np.array([1.0, 2.0, 3.0]), np.nan, 10, spy)

    assert np.isnan(out)
    assert called == []


def test_pointwise_latest_does_not_issue_more_evaluations_as_input_grows() -> None:
    """計算量（§4.1）: 末尾 1 点入口は fn を**捨てる分だけ余計に**呼ばない。

    発行 − 使用 = 0。系列長を変えた 2 点で「入力を増やしても発行が増えない」を固定する
    （回数そのものは焼き込まない）。
    """
    issued = {}
    for length in (10, 1000):
        calls: list[float] = []

        def spy(window: np.ndarray, current: float) -> float:
            calls.append(current)
            return float(window.sum())

        values = np.arange(length, dtype=np.float64)
        marod_bands.causal_pointwise_latest(values[:-1], values[-1], 5, spy)
        issued[length] = len(calls)

    assert issued[10] == issued[1000]
    assert issued[10] > 0


def test_pointwise_supports_the_empirical_rank_of_the_current_bar() -> None:
    """ISSUE-449 §5.3 の用途: 帯内 p = 窓内で当該値未満の割合。"""
    values = np.array([1.0, 2.0, 3.0, 4.0, 2.5])

    out = marod_bands.rolling_causal_pointwise(
        values,
        10,
        lambda window, current: float(np.count_nonzero(window < current)) / window.size,
    )

    assert out[4] == pytest.approx(2 / 4)   # 窓 [1,2,3,4] のうち 2.5 未満は 2 本
