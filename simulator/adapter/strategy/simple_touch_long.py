"""動作確認用の最もシンプルな Long EA。

目的:
    - トリガー条件の成立を最小限で確認する
    - 発注が作られるかどうかを見極める
    - 以後の本格 EA と比較できる

判定:
    prev_low > prev_sma and current_low <= current_sma

実装は SmaTouchLong と同じロジックを簡素化した最小版。
"""
from __future__ import annotations

import math
from typing import Any

from simulator.adapter.strategy.mql5_runtime import (
    math_round,
    normalize_double,
    spec_value,
)
from simulator.domain.order import Order
from simulator.usecase.ports import EntryPriceBasisPort, StrategyPort


class SimpleTouchLong(StrategyPort, EntryPriceBasisPort):
    """最小限の長押し判定で buy 成行を返す EA。"""

    #: 判定の瞬間（`EntryPriceBasisPort`）。当該足の close と sma を読むので、判定は足が
    #: **閉じたあと**でしか成立しない（取れる価格は当該足の終値）。
    entry_price_basis = "close"

    def __init__(self) -> None:
        self._config: Any | None = None
        self._cooldown_until: int | None = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config
        self._cooldown_until = None

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list[Order]:
        if self._config is None:
            raise ValueError("SimpleTouchLong requires on_init() before on_new_bar().")
        if bar_index < 1:
            return []
        if self._cooldown_until is not None and bar_index <= self._cooldown_until:
            return []
        if "buy" in self._held_sides(account):
            return []

        close = indicators.get("close")
        sma = indicators.get("sma_5")
        if bar_index >= len(close) or bar_index >= len(sma):
            return []

        prev_close = float(close.iloc[bar_index - 1])
        curr_close = float(close.iloc[bar_index])
        prev_sma = float(sma.iloc[bar_index - 1])
        curr_sma = float(sma.iloc[bar_index])

        if prev_close > prev_sma and curr_close <= curr_sma:
            self._cooldown_until = bar_index + 2
            return [
                Order(
                    side="buy",
                    kind="market",
                    volume=self._normalize_lot(float(self._config["lot_size"])),
                    price=None,
                    sl=None,
                    tp=None,
                )
            ]
        return []

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
