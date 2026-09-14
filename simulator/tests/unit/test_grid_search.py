"""TDD 単体: GridSearch 辞書順候補列挙・max_candidates 超過拒否（詳細設計 §6.2.1）。

合成 search_space のみを入力（marketdata 非依存・エンジン不要）。
"""
from __future__ import annotations

import pytest

from simulator.usecase.optimize import OptimizeError
from simulator.usecase.optimize_strategies import GridSearch


def test_grid_search_enumerates_in_dictionary_order_right_key_fastest():
    # Arrange: キー昇順 a,b（入力は b,a の順）。右端 b が速く回る。
    search_space = {"b": [1, 2], "a": [10, 20]}
    grid = GridSearch(max_candidates=99)

    # Act
    candidates = list(grid.candidates(search_space))

    # Assert: a 昇順 × b 昇順（右端 b 最下位桁）
    assert candidates == [
        {"a": 10, "b": 1},
        {"a": 10, "b": 2},
        {"a": 20, "b": 1},
        {"a": 20, "b": 2},
    ]


def test_grid_search_each_candidate_key_order_is_sorted_keys():
    # Arrange
    search_space = {"b": [1, 2], "a": [10, 20]}
    grid = GridSearch(max_candidates=99)

    # Act
    candidates = list(grid.candidates(search_space))

    # Assert: 各 dict のキー挿入順が keys 昇順（a, b）
    for c in candidates:
        assert list(c.keys()) == ["a", "b"]


def test_grid_search_theoretical_count_is_product_of_value_list_lengths():
    # Arrange
    search_space = {"a": [1, 2, 3], "b": [1, 2]}
    grid = GridSearch(max_candidates=99)

    # Act / Assert: 3 * 2 = 6
    assert grid.theoretical_count(search_space) == 6


def test_grid_search_rejects_when_theoretical_count_exceeds_max_candidates():
    # Arrange: N_space=6 > max=5（M-2 単一動作：拒否）
    search_space = {"a": [1, 2, 3], "b": [1, 2]}
    grid = GridSearch(max_candidates=5)

    # Act / Assert: 1 件も yield せず OptimizeError
    with pytest.raises(OptimizeError) as exc:
        list(grid.candidates(search_space))
    assert exc.value.context["theoretical"] == 6
    assert exc.value.context["max_candidates"] == 5


def test_grid_search_accepts_when_count_equals_max_candidates():
    # Arrange: N_space=6 == max=6（境界・拒否しない）
    search_space = {"a": [1, 2, 3], "b": [1, 2]}
    grid = GridSearch(max_candidates=6)

    # Act
    candidates = list(grid.candidates(search_space))

    # Assert: 全 6 件
    assert len(candidates) == 6
