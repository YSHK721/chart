"""cache_layout_descriptor — ディスクキャッシュ配置記述子の型（境界・ISSUE-305）。

本モジュールは :class:`CacheLayout`（記述子 DTO）と :class:`CacheLayoutSource`（記述子を導出できる
当事者の最小境界）だけを持ち、**プロジェクト内の他モジュールを一切 import しない**。

分離の理由（ISSUE-305・依存方向）:
    記述子の型は、書込パスの所有者（dwell Store / zp Store / tf-period controller）が依存する
    **内側**の境界である。一方 :func:`market_profile_api.cache_layout.current_layouts` は、その
    所有者たちを列挙して集約する **外側**の合成（Composition Root の仕事）である。両者を 1 モジュール
    に同居させると「型を使う側」と「具象を列挙する側」が同じ名前を指し、

        cache_layout → controller.tf_period_profile_controller → cache_layout

    の依存循環になる（実測: codescan の循環検出）。関数内 import で module ロード時の失敗を避けても、
    循環そのものは消えない。型を本モジュールへ分けることで、依存は所有者 → 記述子の一方向になる。

記述子の意味・不変条件は :mod:`market_profile_api.cache_layout` の module docstring を参照する
（GC 向けの公開契約はそちらが所有する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CacheLayout:
    """1 キャッシュ系統の世代レイアウト記述子（GC はこれのみを参照する）。"""

    name: str
    root: "Path | None"
    gen_depth: int
    current: "frozenset[str]"
    reason: str


@runtime_checkable
class CacheLayoutSource(Protocol):
    """自身のディスク配置から世代記述子を導出できる当事者（GC 向けの最小境界）。

    永続化 Store の本務ポート（:mod:`market_profile_api.compute.store_port`）とは分離する
    （ISP: Store 実装者に GC 都合のメソッドを強制しない）。
    """

    def layout(self) -> CacheLayout:
        """自身の書込パス構成から導いた現行世代記述子を返す。"""
        ...
