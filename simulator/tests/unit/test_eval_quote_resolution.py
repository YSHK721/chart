"""resolve_eval_quote 単体テスト（ISSUE-094 🟡-10b 抽出物の直接検証）。

Account に埋め込まれていた執行クォート規約（旧 Account._eval_price）を usecase 側
（_execution.resolve_eval_quote）へ移送した是正の直接検証。解決した (bid, ask) を
Account.update_floating_pnl_at へ渡した結果が、段階縮退シム update_floating_pnl(bar)
（＝旧 _eval_price 経路）と byte-identical であることを固定する。
"""
from __future__ import annotations

from dataclasses import dataclass

from simulator.domain.account import Account
from simulator.domain.position import Position
from simulator.usecase._execution import resolve_eval_quote


@dataclass
class _Bar:
    close: float
    spread: int = 0


class TestResolveEvalQuote:
    def test_close_basis_bid_equals_ask_equals_close(self):
        bar = _Bar(close=101.0, spread=10)
        bid, ask = resolve_eval_quote(bar, basis="close", point_size=0.1)
        assert bid == 101.0
        assert ask == 101.0

    def test_bid_ask_basis_ask_adds_spread_times_point(self):
        bar = _Bar(close=101.0, spread=10)
        bid, ask = resolve_eval_quote(bar, basis="bid_ask", point_size=0.1)
        assert bid == 101.0
        assert ask == 101.0 + 10 * 0.1


class TestEquivalenceWithLegacyShim:
    """resolve_eval_quote + update_floating_pnl_at == update_floating_pnl(bar)。"""

    def _accounts(self, positions, basis, point_size):
        legacy = Account(
            balance=10_000.0, contract_size=100_000.0,
            open_positions=list(positions),
            floating_pnl_basis=basis, point_size=point_size,
        )
        moved = Account(
            balance=10_000.0, contract_size=100_000.0,
            open_positions=list(positions),
            floating_pnl_basis=basis, point_size=point_size,
        )
        return legacy, moved

    def _check(self, positions, basis):
        bar = _Bar(close=51234.5, spread=100)
        point_size = 0.1
        legacy, moved = self._accounts(positions, basis, point_size)
        # 段階縮退シム（bar 経路）
        legacy.update_floating_pnl(bar)
        # usecase 移送後の経路
        bid, ask = resolve_eval_quote(bar, basis=basis, point_size=point_size)
        moved.update_floating_pnl_at(bid=bid, ask=ask)
        assert moved.floating_pnl == legacy.floating_pnl  # 完全一致

    def test_close_buy(self):
        self._check([Position(side="buy", volume=1.0, entry_price=51000.0)], "close")

    def test_close_sell(self):
        self._check([Position(side="sell", volume=1.0, entry_price=51000.0)], "close")

    def test_bid_ask_buy(self):
        self._check([Position(side="buy", volume=0.7, entry_price=51000.0)], "bid_ask")

    def test_bid_ask_sell(self):
        self._check([Position(side="sell", volume=0.7, entry_price=51000.0)], "bid_ask")

    def test_bid_ask_mixed_multiposition(self):
        positions = [
            Position(side="buy", volume=0.3, entry_price=51100.0),
            Position(side="sell", volume=0.5, entry_price=51050.0),
            Position(side="buy", volume=0.2, entry_price=51230.0),
        ]
        self._check(positions, "bid_ask")
