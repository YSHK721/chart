"""UC Port 抽象: 探索アルゴリズム／目的関数の差し替え IF（基本設計 §6.1・FR-O2/O3）。

ParameterSearchPort（探索戦略）と ObjectivePort（目的関数）を typing.Protocol で
定義する。標準ライブラリのみ依存（pandas/main/adapter/domain も import しない）。
committed ports.py は編集しない（C2）。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

# ParamSet: build_interactor へ **params でマージ可能な部分写像（不変ビュー）。
ParamSet = Mapping[str, Any]


@runtime_checkable
class ParameterSearchPort(Protocol):
    """探索アルゴリズムの抽象（FR-O2・差替可能・OCP）。"""

    def candidates(self, search_space: "Mapping[str, list]") -> "Iterable[ParamSet]":
        """探索空間 -> 候補 ParamSet の決定論的順序付きイテラブル（FO-02）。"""
        ...

    def theoretical_count(self, search_space: "Mapping[str, list]") -> int:
        """列挙前に算出する理論候補数（grid=N_space / random=min(n_samples,N_space)）。"""
        ...


@runtime_checkable
class ObjectivePort(Protocol):
    """目的関数の抽象（FR-O3・差替可能・OCP）。

    IS の BacktestStats から「大きいほど良い」単一スカラを返す（argmax 規約）。
    返値の有限性判定（math.isfinite）は UC 側で行う（C-1）。
    """

    name: str

    def score(self, stats: Any) -> float:
        """IS BacktestStats -> 目的値（float・大きいほど良い）。"""
        ...
