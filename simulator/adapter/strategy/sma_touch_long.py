"""M1 で SMA5 に最初に安値がタッチした時にロングする戦略。

既存ファイルを改変せず、新規追加する専用戦略。判定は:
    prev_low > prev_sma and current_low <= current_sma
を満たしたときに買い成行を出す。

これは「価格が SMA を上から触り、その後安値が SMA に接触した瞬間」を
長いエントリのトリガーとする最小実装であり、既存の generic strategy に
依存しない独立した StrategyPort として定義する。
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


class SmaTouchLong(StrategyPort, EntryPriceBasisPort):
    """M1 で安値が SMA5 に初めてタッチした時に買いを入れる戦略。"""

    #: 判定の瞬間（`EntryPriceBasisPort`）。当該足の close と sma を読む
    #: （close[bar_index] / sma[bar_index]）ので、判定は足が**閉じたあと**でしか成立せず、
    #: そのとき取れる価格は当該足の終値である。
    entry_price_basis = "close"

    def __init__(self) -> None:
        self._config: Any | None = None
        self._cooldown_until: int | None = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config
        self._cooldown_until = None

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list[Order]:
        if self._config is None:
            raise ValueError("SmaTouchLong requires on_init() before on_new_bar().")
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
