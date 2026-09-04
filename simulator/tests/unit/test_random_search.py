"""TDD 単体: RandomSearch seed 固定再現・整数インデックス抽出・全件・grid 同一基準順序
（詳細設計 §6.2.2・条件3）。
"""
from __future__ import annotations

import pytest

from simulator.usecase.optimize import OptimizeError
from simulator.usecase.optimize_strategies import GridSearch, RandomSearch


def _space():
    # N_space = 5 * 2 = 10（キー昇順 a,b）
    return {"a": [1, 2, 3, 4, 5], "b": [10, 20]}


def test_random_search_seed_fixed_yields_identical_candidate_sequence():
    # Arrange
    space = _space()
    rs1 = RandomSearch(seed=42, n_samples=3, max_candidates=99)
    rs2 = RandomSearch(seed=42, n_samples=3, max_candidates=99)

    # Act
    c1 = list(rs1.candidates(space))
    c2 = list(rs2.candidates(space))

    # Assert: seed 固定で byte 同一の ParamSet 列（High-3）
    assert c1 == c2
    assert len(c1) == 3


def test_random_search_enumerates_selected_indices_in_ascending_order():
    # Arrange: 選択 idx を sorted した昇順で復号（FO-02 後条件）
    space = _space()
    grid_all = list(GridSearch(max_candidates=99).candidates(space))
    rs = RandomSearch(seed=7, n_samples=4, max_candidates=99)

    # Act
    rnd = list(rs.candidates(space))

    # Assert: 各候補は grid 全列挙のどれかであり、grid 列挙中の出現順（=idx 昇順）を保つ
    grid_positions = [grid_all.index(c) for c in rnd]
    assert grid_positions == sorted(grid_positions)


def test_random_search_takes_all_when_n_samples_exceeds_space_size():
    # Arrange: n=100 > N_space=10 -> 全件（k=min(100,10)=10）
    space = _space()
    rs = RandomSearch(seed=1, n_samples=100, max_candidates=99)

    # Act
    candidates = list(rs.candidates(space))

    # Assert: 全 10 件・重複なし
    assert len(candidates) == 10
    assert candidates == list(GridSearch(max_candidates=99).candidates(space))


def test_random_search_theoretical_count_is_min_n_samples_and_space():
    # Arrange
    space = _space()  # N_space=10
    rs = RandomSearch(seed=1, n_samples=3, max_candidates=99)

    # Act / Assert
    assert rs.theoretical_count(space) == 3
    assert RandomSearch(seed=1, n_samples=100, max_candidates=99).theoretical_count(space) == 10


def test_random_search_rejects_when_theoretical_count_exceeds_max_candidates():
    # Arrange: k=min(6,N=10)=6 > max=5
    space = _space()
    rs = RandomSearch(seed=1, n_samples=6, max_candidates=5)

    # Act / Assert
    with pytest.raises(OptimizeError) as exc:
        list(rs.candidates(space))
    assert exc.value.context["algo"] == "random"


def test_random_search_shares_baseline_order_with_grid_product():
    # Arrange: random で選んだ idx の復号が grid 列挙の同 idx 要素と一致（基準順序共有・条件3）
    space = _space()
    grid_all = list(GridSearch(max_candidates=99).candidates(space))
    rs = RandomSearch(seed=99, n_samples=10, max_candidates=99)  # 全件で完全一致

    # Act
    rnd = list(rs.candidates(space))

    # Assert: 全件選択時は grid 全列挙と完全一致（同一基準順序）
    assert rnd == grid_all
