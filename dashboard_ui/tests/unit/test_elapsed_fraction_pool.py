"""§5.3.3 同じ経過割合の分布（T-8 裁定: 丸め禁止・素材の最小単位で厳密に同経過）を固定する。

根本原因（§5.3.3）は「**部分和を完全和の分布へ当てている**」＝比較集合の取り違えである。
形成途中の足を確定足の分布へ当てると必ず極小に出る（1h の最初の 20 分はどんなに活況でも
最も冷たい色になる）。是正は比較集合を揃えるだけで、足内の tick 到来プロファイルを**仮定しない**。

T-8: 経過割合の丸め（binning）は不採用（0.05/0.10 刻みで p90 |Δp| 0.10〜0.15 ＝ バイアスの
再導入）。tf >= 5m は 1m 境界（k = 完了 1m 本数）で厳密に突合し、部分和は 1m 系列の
prefix cumsum から導出する（保持は cumsum 1 本・ティック数に非比例）。
"""
from __future__ import annotations

import numpy as np
import pytest

from dashboard_ui.domain.elapsed_fraction_pool import ElapsedFractionPool


def _pool(*bars: list[float]) -> ElapsedFractionPool:
    keys: list[int] = []
    values: list[float] = []
    for index, bar in enumerate(bars):
        keys.extend([index] * len(bar))
        values.extend(bar)
    return ElapsedFractionPool.from_units(keys, values)


class TestConstruction:
    def test_bars_are_grouped_by_their_key(self) -> None:
        pool = _pool([1.0, 2.0, 3.0], [4.0, 5.0])

        assert pool.bar_count == 2
        assert pool.bar_lengths == (3, 2)
        assert pool.unit_count == 5

    def test_an_empty_series_yields_an_empty_pool(self) -> None:
        pool = ElapsedFractionPool.from_units([], [])

        assert pool.bar_count == 0
        assert pool.unit_count == 0

    def test_mismatched_lengths_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            ElapsedFractionPool.from_units([0, 0], [1.0])

    def test_a_reappearing_bar_key_is_rejected(self) -> None:
        """時刻順に連続していない素材を無言で受け取らない（§11-4 の教訓）。"""
        with pytest.raises(ValueError):
            ElapsedFractionPool.from_units([0, 1, 0], [1.0, 2.0, 3.0])

    def test_a_non_finite_unit_value_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ElapsedFractionPool.from_units([0, 0], [1.0, float("nan")])


class TestPartialSum:
    def test_the_partial_sum_is_the_sum_of_the_first_k_units_of_that_bar(self) -> None:
        pool = _pool([1.0, 2.0, 3.0, 4.0], [10.0, 20.0])

        assert pool.partial_sum(0, 3) == pytest.approx(6.0)
        assert pool.partial_sum(1, 2) == pytest.approx(30.0)

    def test_the_full_bar_is_the_partial_sum_at_its_own_length(self) -> None:
        pool = _pool([1.0, 2.0, 3.0])

        assert pool.partial_sum(0, 3) == pytest.approx(6.0)

    @pytest.mark.parametrize("k", [0, -1, 5])
    def test_an_out_of_range_elapsed_count_is_rejected(self, k: int) -> None:
        """境界値: k は 1..その足の長さ。範囲外を NaN で誤魔化さない。"""
        pool = _pool([1.0, 2.0, 3.0, 4.0])

        with pytest.raises(ValueError):
            pool.partial_sum(0, k)

    def test_an_out_of_range_bar_index_is_rejected(self) -> None:
        pool = _pool([1.0, 2.0])

        with pytest.raises(IndexError):
            pool.partial_sum(3, 1)


class TestComparisonSet:
    def test_the_comparison_set_is_the_partial_sums_of_bars_at_the_same_elapsed_count(
        self,
    ) -> None:
        """同じ経過（k 単位まで進んだ）過去の足だけを比較集合にする。"""
        pool = _pool([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], [100.0, 200.0, 300.0])

        np.testing.assert_allclose(pool.partial_sums_at(2), [3.0, 30.0, 300.0])

    def test_bars_shorter_than_the_elapsed_count_are_excluded(self) -> None:
        """厳密同経過: k に到達していない足は比較対象にならない（丸めて混ぜない）。"""
        pool = _pool([1.0, 2.0, 3.0], [10.0], [100.0, 200.0])

        np.testing.assert_allclose(pool.partial_sums_at(2), [3.0, 300.0])

    def test_an_elapsed_count_beyond_every_bar_yields_an_empty_set(self) -> None:
        pool = _pool([1.0], [2.0])

        assert pool.partial_sums_at(5).size == 0

    def test_the_comparison_set_is_not_writable_by_the_caller(self) -> None:
        """キャッシュを共有するため、呼び出し側が書き換えられてはならない。"""
        pool = _pool([1.0, 2.0], [3.0, 4.0])

        with pytest.raises(ValueError):
            pool.partial_sums_at(1)[0] = 99.0

    @pytest.mark.parametrize("k", [0, -1])
    def test_an_invalid_elapsed_count_is_rejected(self, k: int) -> None:
        with pytest.raises(ValueError):
            _pool([1.0, 2.0]).partial_sums_at(k)


class TestIncrementalUpdate:
    def test_closing_a_unit_extends_the_current_bar(self) -> None:
        pool = _pool([1.0, 2.0])

        pool.close_unit(0, 3.0)

        assert pool.bar_lengths == (3,)
        assert pool.partial_sum(0, 3) == pytest.approx(6.0)

    def test_closing_a_unit_with_a_new_key_starts_a_new_bar(self) -> None:
        pool = _pool([1.0, 2.0])

        pool.close_unit(1, 5.0)

        assert pool.bar_count == 2
        assert pool.bar_lengths == (2, 1)

    def test_closing_a_unit_invalidates_the_comparison_set(self) -> None:
        """新しい素材が入ったのに古い窓を返し続けてはならない。"""
        pool = _pool([1.0, 2.0], [3.0, 4.0])
        before = pool.partial_sums_at(1).copy()

        pool.close_unit(2, 7.0)

        np.testing.assert_allclose(before, [1.0, 3.0])
        np.testing.assert_allclose(pool.partial_sums_at(1), [1.0, 3.0, 7.0])

    def test_reopening_an_earlier_bar_is_rejected(self) -> None:
        pool = _pool([1.0], [2.0])

        with pytest.raises(ValueError):
            pool.close_unit(0, 3.0)
