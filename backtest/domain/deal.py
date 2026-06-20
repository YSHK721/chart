"""E-Deal: 約定明細（Value Object・CLEAN_ARCH §4 / METRICS §5.2）。

不変条件: direction in {in, out}。profit は式で一意（METRICS §5.2）:
    profit = (close - entry) * sign * lot * contract_size + swap + commission
    sign は buy=+1 / sell=-1。

振る舞いなし（不変データ）。profit の算出は決済時のファクトリ
:meth:`Deal.from_close` で行い、インスタンスは確定値のみを保持する。
"""
from __future__ import annotations

from dataclasses import dataclass

from backtest.domain._shared import round_profit, sign_of
from backtest.domain.exceptions import ExecutionError

# direction は Deal 固有の語彙のため当モジュールに留める（YAGNI: 単一利用）。
_DIRECTIONS = frozenset({"in", "out"})


@dataclass(frozen=True)
class Deal:
    direction: str
    price: float
    volume: float
    profit: float
    swap: float
    commission: float

    def __post_init__(self) -> None:
        if self.direction not in _DIRECTIONS:
            raise ExecutionError(
                "direction は {in, out} のいずれか", context={"direction": self.direction}
            )

    @classmethod
    def from_close(
        cls,
        *,
        side: str,
        entry_price: float,
        close_price: float,
        volume: float,
        contract_size: float,
        swap: float,
        commission: float,
        profit_round_digits: "int | None" = None,
    ) -> "Deal":
        """決済約定を生成し、METRICS §5.2 の式で profit を確定する。

        profit_round_digits 指定時は profit を口座通貨桁へ丸める（既定 None＝素値＝
        後方互換）。TradeRecord.pnl と同一の round_profit を共有し balance/stats を一致させる。
        """
        # METRICS §5.2: (close - entry) * sign * lot * contract_size + swap + commission
        profit = round_profit(
            (close_price - entry_price) * sign_of(side) * volume * contract_size
            + swap
            + commission,
            profit_round_digits,
        )
        return cls(
            direction="out",
            price=close_price,
            volume=volume,
            profit=profit,
            swap=swap,
            commission=commission,
        )
