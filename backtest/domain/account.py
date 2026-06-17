"""E-Account: 口座状態（CLEAN_ARCH §4 / METRICS §5.1）。

関係式:
    equity = balance + floating_pnl + swap + commission
    margin_level = equity / margin * 100   （margin == 0 のとき ∞）

公開振る舞い:
    apply_deal(deal)           確定損益を balance に反映する。
    update_floating_pnl(bar)   保有ポジションの含み損益を bar.close で再評価する。
    margin_level() -> float    証拠金維持率を返す。

設計判断（frozen 方針からの逸脱）:
    Account は run 全体で状態遷移する集約であり、CLEAN_ARCH §4 が apply_deal /
    update_floating_pnl という状態変更振る舞いを明示している。このため値オブジェクト
    （frozen）方針の例外として可変 dataclass とする。domain は numpy のみ依存可。

apply_deal は deal.profit（METRICS §5.2 で swap/commission 込みの純額）を balance に
加算する。floating_pnl は update_floating_pnl で保有ポジションから再計算する。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from backtest.domain.deal import Deal
from backtest.domain.position import Position


@dataclass
class Account:
    balance: float
    margin: float = 0.0
    contract_size: float = 100_000.0
    floating_pnl: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    open_positions: list[Position] = field(default_factory=list)

    @property
    def equity(self) -> float:
        # METRICS §5.1: equity = balance + floating_pnl + swap + commission
        return self.balance + self.floating_pnl + self.swap + self.commission

    def margin_level(self) -> float:
        # METRICS §5.1: equity / margin * 100。margin == 0 は ∞ 扱い
        if self.margin == 0:
            return math.inf
        return self.equity / self.margin * 100.0

    def apply_deal(self, deal: Deal) -> None:
        """確定損益（profit は METRICS §5.2 の純額）を balance に反映する。"""
        self.balance += deal.profit

    def update_floating_pnl(self, bar: Any) -> None:
        """保有ポジションの含み損益を bar.close で再評価する（METRICS §5.1）。"""
        # TODO(🟡-2): close 評価は暫定。usecase 接続時に side 別 Bid/Ask 価格解決へ要変更（PROCESS §6）
        self.floating_pnl = sum(
            pos.floating_pnl(bar.close, self.contract_size) for pos in self.open_positions
        )
