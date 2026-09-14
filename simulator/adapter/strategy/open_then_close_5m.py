from __future__ import annotations

import math
from typing import Any

from simulator.adapter.strategy.mql5_runtime import (
    math_round,
    normalize_double,
    spec_value,
)
from simulator.domain.order import Order
from simulator.usecase.ports import StrategyPort


class OpenThenClose5mLong(StrategyPort):
    """始値でロングし、1本後の終値で決済する最小 EA。"""

    def __init__(self) -> None:
        self._config: Any | None = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list[Order]:
        if self._config is None:
            raise ValueError("OpenThenClose5mLong requires on_init() before on_new_bar().")
        if "buy" in self._held_sides(account):
            return []

        close = indicators.get("close")
        exit_index = bar_index + 5
        if close is None or exit_index >= len(close):
            return []

        entry_price = float(close.iloc[bar_index])
        exit_price = float(close.iloc[exit_index])
        if entry_price == 0.0:
            return []

        return [
            Order(
                side="buy",
                kind="market",
                volume=self._normalize_lot(float(self._config["lot_size"])),
                price=None,
                sl=None,
                tp=exit_price,
            )
        ]

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"

    def _normalize_lot(self, lot: float) -> float:
        cfg = self._config
        if cfg is None:
            return lot
        step = spec_value(cfg, "volume_step")
        volume_min = spec_value(cfg, "volume_min")
        v = lot
        if step > 0.0:
            v = math_round(v / step) * step
        if v < volume_min:
            v = volume_min
        volume_max = spec_value(cfg, "volume_max")
        if volume_max > 0.0 and v > volume_max:
            v = volume_max
        digits = int(math.ceil(-math.log10(step))) if step > 0.0 else 2
        if digits < 0:
            digits = 0
        return normalize_double(v, digits)

    @staticmethod
    def _held_sides(account: Any) -> set[str]:
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}
