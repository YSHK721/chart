"""UC Port 既定実装: GridSearch/RandomSearch・PF/Net/Sharpe/Recovery Objective。

optimize_ports の Protocol を満たす具体実装（OCP：新アルゴリズム追加で既存無改変）。
標準ライブラリ（itertools/random/math）＋ usecase.run_is_oos.extract_metrics のみ依存。
pandas/main/adapter は import しない（クリーンアーキ依存方向）。
"""
from __future__ import annotations

import itertools
import random
from typing import Any, Iterable, Mapping

from simulator.usecase.optimize import OptimizeError  # 上限超過の明示中断（戦略->optimize 単方向）


# --- 辞書順序規約（決定論の基礎・NFR-OD1） -------------------------------------

def _ordered_keys(search_space: "Mapping[str, list]") -> "list[str]":
    return sorted(search_space.keys())


def _space_size(search_space: "Mapping[str, list]", keys: "list[str]") -> int:
    n = 1
    for k in keys:
        n *= len(search_space[k])
    return n


def _decode_index(idx: int, search_space: "Mapping[str, list]", keys: "list[str]") -> dict:
    """基準インデックス -> ParamSet（keys を右端最下位桁とする混合基数復号・決定論）。"""
    out: dict = {}
    rem = idx
    for k in reversed(keys):  # 右端キーが最下位桁
        size = len(search_space[k])
        out[k] = search_space[k][rem % size]
        rem //= size
    return {k: out[k] for k in keys}  # 挿入順を keys 昇順へ整える


# 上限超過の明示中断メッセージ（grid/random で共通・M-2）。
_EXCEED_MESSAGE = "theoretical candidate count exceeds max_candidates"


def _reject_if_exceeds(theoretical: int, max_candidates: int, **extra: Any) -> None:
    """理論候補数が上限を超える場合に OptimizeError を送出（M-2 単一動作：拒否）。

    extra に algo 等の中央化されない内訳を渡す（grid/random で context が異なる）。
    呼出側 candidates はジェネレータのため、本送出は初回反復時に評価される（遅延性保持）。
    """
    if theoretical > max_candidates:
        raise OptimizeError(
            _EXCEED_MESSAGE,
            context={
                "theoretical": theoretical,
                "max_candidates": max_candidates,
                **extra,
            },
        )


# --- 探索戦略 ----------------------------------------------------------------

class GridSearch:
    """直積全列挙（辞書順）。N_cand = N_space（基本設計 FO-02 grid）。"""

    def __init__(self, *, max_candidates: int) -> None:
        self.max_candidates = max_candidates  # 必須（既定なし・M-3）

    def theoretical_count(self, search_space: "Mapping[str, list]") -> int:
        keys = _ordered_keys(search_space)
        return _space_size(search_space, keys)

    def candidates(self, search_space: "Mapping[str, list]") -> "Iterable[dict]":
        keys = _ordered_keys(search_space)
        n_space = _space_size(search_space, keys)
        _reject_if_exceeds(n_space, self.max_candidates, algo="grid")  # M-2
        for combo in itertools.product(*[search_space[k] for k in keys]):
            yield {k: v for k, v in zip(keys, combo)}  # 辞書順・挿入順=keys


class RandomSearch:
    """離散候補集合からの整数インデックス非復元抽出（基本設計 FO-02 random・High-3）。"""

    def __init__(self, *, seed: int, n_samples: int, max_candidates: int) -> None:
        self.seed = seed
        self.n_samples = n_samples
        self.max_candidates = max_candidates  # M-3 必須

    def theoretical_count(self, search_space: "Mapping[str, list]") -> int:
        keys = _ordered_keys(search_space)
        n_space = _space_size(search_space, keys)
        return min(self.n_samples, n_space)

    def candidates(self, search_space: "Mapping[str, list]") -> "Iterable[dict]":
        keys = _ordered_keys(search_space)
        n_space = _space_size(search_space, keys)
        k = min(self.n_samples, n_space)  # n_samples>N_space は全件
        _reject_if_exceeds(  # M-2
            k,
            self.max_candidates,
            algo="random",
            n_space=n_space,
            n_samples=self.n_samples,
        )
        rng = random.Random(self.seed)  # seed 固定で決定論
        idxs = rng.sample(range(n_space), k=k)  # 整数インデックス非復元抽出
        for idx in sorted(idxs):  # 選択インデックスの昇順で復号
            yield _decode_index(idx, search_space, keys)


# --- 目的関数（PF/Net/Sharpe/Recovery） --------------------------------------

class _FieldObjective:
    """BacktestStats の単一フィールドを score とする基底（大きいほど良い・FO-04）。"""

    def __init__(self, name: str, field: str) -> None:
        self.name = name
        self._field = field

    def score(self, stats: Any) -> float:
        from simulator.usecase.run_is_oos import extract_metrics

        return extract_metrics(stats, (self._field,))[self._field]


class PfObjective(_FieldObjective):
    def __init__(self) -> None:
        super().__init__("profit_factor", "profit_factor")


class NetProfitObjective(_FieldObjective):
    def __init__(self) -> None:
        super().__init__("profit", "profit")


class SharpeObjective(_FieldObjective):
    def __init__(self) -> None:
        super().__init__("sharpe_ratio", "sharpe_ratio")


class RecoveryObjective(_FieldObjective):
    def __init__(self) -> None:
        super().__init__("recovery_factor", "recovery_factor")
