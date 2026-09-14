"""足内更新の可否（増分器の宣言有無）をライブ側の判定から引く。

唯一源はライブ側の毎ティック末尾値アダプタが持つ増分器の宣言判定である。写しを作らない:
増分器が付いた／外れたときに表示だけ古くなると、更新粒度（§7 で表に出すと決めた項目）が
実態と食い違い、無言の縮退になる。

本モジュールが adapter にあるのは、bridge と指標計算 Facade を知ってよいのが adapter だけ
だからである（依存方向の検定 R3）。main は組み立てるだけで、技術を知らない。
"""
from __future__ import annotations

from typing import Any, Mapping


class IntrabarCapabilityGateway:
    """`(indicator_id, variant, params) -> bool` として呼べる判定器。"""

    def __init__(self, *, bridge: Any = None) -> None:
        self._bridge = bridge
        self._is_incremental = None

    def __call__(
        self, indicator_id: str, variant: str, params: "Mapping[str, object]"
    ) -> bool:
        """その指標が足内更新できるか（増分器を宣言しているか）。"""
        return bool(self._resolve()(indicator_id, variant, dict(params)))

    def _resolve(self):
        if self._is_incremental is None:
            if self._bridge is None:
                from indigators.indicator_ui import api_loader  # 遅延: 技術隔離

                self._bridge = api_loader.load_compute()
            from adapter.compute.live_tick_tails import is_incremental  # 遅延: 技術隔離

            self._is_incremental = is_incremental
        return self._is_incremental
