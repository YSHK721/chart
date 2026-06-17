"""E-Position: 建玉（CLEAN_ARCH §4 / METRICS §5.1, §5.3）。

不変条件: volume > 0, entry_price > 0, side in {buy, sell}。

公開振る舞い:
    floating_pnl(price, contract_size) -> float
        METRICS §5.1: (price - entry) * lot * contract_size * sign
        sign は buy=+1 / sell=-1。
    required_margin(leverage, contract_size) -> float
        METRICS §5.3: lot * contract_size * entry / leverage

設計判断: required_margin は contract_size を引数で受ける（spec 表記
    ``required_margin(leverage)`` だが §5.3 の式は contract_size を要するため、
    floating_pnl と同様に引数化して整合させる）。
"""
from __future__ import annotations

from dataclasses import dataclass

from backtest.domain._shared import SIDES, sign_of
from backtest.domain.exceptions import ExecutionError


@dataclass(frozen=True)
class Position:
    side: str
    volume: float
    entry_price: float

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ExecutionError("side は {buy, sell} のいずれか", context={"side": self.side})
        if self.volume <= 0:
            raise ExecutionError("volume は正である必要があります", context={"volume": self.volume})
        if self.entry_price <= 0:
            raise ExecutionError(
                "entry_price は正である必要があります", context={"entry_price": self.entry_price}
            )

    def floating_pnl(self, price: float, contract_size: float) -> float:
        # METRICS §5.1: (price - entry) * lot * contract_size * sign
        return (price - self.entry_price) * self.volume * contract_size * sign_of(self.side)

    def required_margin(self, leverage: float, contract_size: float) -> float:
        # METRICS §5.3: lot * contract_size * entry / leverage
        return self.volume * contract_size * self.entry_price / leverage
