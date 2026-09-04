"""P-4 区分の境目（BreakpointSource）の実装群とレジストリ。

指標側が実装するのは `breakpoints()` **1 メソッドだけ**である（§5.5.3・ISP）。逆関数の
数式は指標ごとに手書きしない（逆写像は `dashboard_ui.domain.price_value_map` が唯一所有）。

§5.5.1 の「価格へ逆算できる指標」は**列挙で書かない**。提供できない指標（tick 数は
価格の関数ではない）はレジストリに**キーが無い**という形で構造に現れる（§8 OCP / LSP）。
"""
from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from dashboard_ui.adapter.breakpoints.marod import MarodBreakpoints
from dashboard_ui.adapter.breakpoints.profit_rsi import ProfitRsiBreakpoints
from dashboard_ui.domain.bar import Bar


@runtime_checkable
class BreakpointSource(Protocol):
    """adapter 側の面（usecase 向けの P-4 に `previous_value` を加えたもの）。

    `previous_value` は上下分岐の高さ（前バーの適用価格）を答える。分岐を持たない指標も
    同じ面を持ち None を返す＝呼ぶ側が指標名で分岐しない（LSP）。
    """

    def breakpoints(
        self, *, bar: Bar, params: "Mapping[str, object]", prev_value: "float | None"
    ) -> "tuple[float, ...]": ...

    def previous_value(
        self, *, bar: Bar, params: "Mapping[str, object]"
    ) -> "float | None": ...


class BreakpointRegistry:
    """指標 → BreakpointSource。提供できない指標はキーが無い。"""

    def __init__(self) -> None:
        marod = MarodBreakpoints()
        self._sources: "dict[str, BreakpointSource]" = {
            "ma_marod": marod,
            "btlm_trail_marod": marod,
            "profit_rsi": ProfitRsiBreakpoints(),
        }

    def resolve(self, indicator_id: str) -> "BreakpointSource | None":
        """無ければ None（＝価格へ逆算できない＝§5.5 の対象外が構造で表れる）。"""
        return self._sources.get(indicator_id)

    def invertible_ids(self) -> "frozenset[str]":
        """§7.1 の契約テストが読む集合（`resolve` と同じ辞書から導く＝第 2 定義を作らない）。"""
        return frozenset(self._sources)
