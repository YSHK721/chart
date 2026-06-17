"""E-TradeRecord: 確定トレード（往復・Value Object・CLEAN_ARCH §4）。

不変条件:
    exit_time >= entry_time（違反時 TimeOrderError）
    exit_reason in {sl, tp, reverse, expire}（PROCESS §6・違反時 DataError）

公開振る舞い:
    pnl() -> float
        METRICS §5.2: (exit - entry) * sign * lot * contract_size + swap + commission
        sign は buy=+1 / sell=-1。
    is_win() -> bool   pnl() > 0（同値は非勝ち）。
    is_long() -> bool  side == "buy"。

時刻型は numpy.datetime64 または epoch int（pd.Timestamp 禁止）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backtest.domain._shared import sign_of
from backtest.domain.exceptions import DataError, TimeOrderError

# exit_reason は TradeRecord 固有の語彙のため当モジュールに留める（YAGNI: 単一利用）。
_EXIT_REASONS = frozenset({"sl", "tp", "reverse", "expire"})


@dataclass(frozen=True)
class TradeRecord:
    side: str
    volume: float
    entry_time: Any
    exit_time: Any
    entry_price: float
    exit_price: float
    contract_size: float
    swap: float
    commission: float
    exit_reason: str

    def __post_init__(self) -> None:
        if self.exit_time < self.entry_time:
            raise TimeOrderError(
                "exit_time は entry_time 以上である必要があります",
                context={"entry_time": self.entry_time, "exit_time": self.exit_time},
            )
        if self.exit_reason not in _EXIT_REASONS:
            raise DataError(
                "exit_reason は {sl, tp, reverse, expire} のいずれか",
                context={"exit_reason": self.exit_reason},
            )

    def pnl(self) -> float:
        # METRICS §5.2: (exit - entry) * sign * lot * contract_size + swap + commission
        return (
            (self.exit_price - self.entry_price)
            * sign_of(self.side)
            * self.volume
            * self.contract_size
            + self.swap
            + self.commission
        )

    def is_win(self) -> bool:
        return self.pnl() > 0  # 同値（== 0）は非勝ち

    def is_long(self) -> bool:
        return self.side == "buy"
