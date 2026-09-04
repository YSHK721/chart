"""usecase 層の境界（Port）抽象：被覆検定・SPA（詳細設計 §4.1）。

検定の具象（numpy・math.erf/erfc）は adapter/validation 側。usecase は p 値 float のみ受ける。
"""
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class BacktestTestPort(Protocol):
    def kupiec(self, hit_series: Sequence[int], alpha: float = 0.05) -> float:
        """ストップ被覆 POF 尤度比検定 → χ²(1) p 値。hit∈{0,1}。"""
        ...

    def christoffersen_independence(self, hit_series: Sequence[int]) -> float:
        """例外の独立性（マルコフ1次）尤度比検定 → χ²(1) p 値。"""
        ...


@runtime_checkable
class SpaTestPort(Protocol):
    def spa_pvalue(
        self,
        f_matrix: Sequence[Sequence[float]],
        *,
        seed: int,
        B: int = 5000,
    ) -> float:
        """Hansen(2005) SPA_c consistent p 値（定常ブート・seed 固定）。"""
        ...
