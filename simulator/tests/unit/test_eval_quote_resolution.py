"""resolve_eval_quote 単体テスト（ISSUE-094 🟡-10b 抽出物の直接検証）。

Account に埋め込まれていた執行クォート規約（旧 Account._eval_price）を usecase 側
（_execution.resolve_eval_quote）へ移送した是正の直接検証。

ISSUE-095 項目2: Account 側の bid/ask basis シム分岐（update_floating_pnl(bar)）を除去し
bar シムを close 固定へ縮退させたため、旧「段階縮退シムとの byte 同値」検証は成立しなく
なった。本テストは legacy シム比較を廃し、本番経路そのもの（usecase が
resolve_eval_quote で (bid, ask) を解決し Account.update_floating_pnl_at で合算する）が
決済価格基準（買い=Bid / 売り=Ask=close+spread×point）の含み損益を正しく算出することを
直接固定する。
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


class TestProductionEvalQuotePath:
    """本番経路 resolve_eval_quote + Account.update_floating_pnl_at の直接検証（ISSUE-095 項目2）。

    含み損益は決済価格基準で評価する: 買い保有は Bid=bar.close、売り保有は Ask で評価し、
    close 基準は Ask=close（spread 無視）、bid_ask 基準は Ask=close+spread×point_size。
    期待値は resolve_eval_quote を用いずに (bid, ask) を独立に計算して合算し（非トートロジー
    な参照）、本番経路の算出と一致することを固定する。
    """

    CLOSE = 51234.5
    SPREAD = 100
    POINT = 0.1
    CONTRACT = 100_000.0

    def _floating_via_production_path(self, positions, basis):
        """本番経路: usecase が (bid, ask) を解決し Account が update_floating_pnl_at で合算。"""
        acc = Account(
            balance=10_000.0, contract_size=self.CONTRACT,
            open_positions=list(positions),
        )
        bar = _Bar(close=self.CLOSE, spread=self.SPREAD)
        bid, ask = resolve_eval_quote(bar, basis=basis, point_size=self.POINT)
        acc.update_floating_pnl_at(bid=bid, ask=ask)
        return acc.floating_pnl

    def _expected_floating(self, positions, basis):
        """独立参照: (bid, ask) を resolve_eval_quote を介さず直接計算して合算する。"""
        bid = self.CLOSE
        ask = self.CLOSE + self.SPREAD * self.POINT if basis == "bid_ask" else self.CLOSE
        return sum(
            p.floating_pnl(bid if p.side == "buy" else ask, self.CONTRACT)
            for p in positions
        )

    def _check(self, positions, basis):
        actual = self._floating_via_production_path(positions, basis)
        expected = self._expected_floating(positions, basis)
        assert actual == expected  # 完全一致

    def test_close_buy(self):
        self._check([Position(side="buy", volume=1.0, entry_price=51000.0)], "close")

    def test_close_sell(self):
        # close 基準では売りも Ask=close ゆえ spread の影響を受けない（従来不変）。
        self._check([Position(side="sell", volume=1.0, entry_price=51000.0)], "close")

    def test_bid_ask_buy(self):
        # 買いは Bid=close ゆえ bid_ask でも close 評価と同値（spread 非依存）。
        self._check([Position(side="buy", volume=0.7, entry_price=51000.0)], "bid_ask")

    def test_bid_ask_sell(self):
        # 売りは Ask=close+spread×point で悲観化評価される（bid_ask 基準の核心）。
        self._check([Position(side="sell", volume=0.7, entry_price=51000.0)], "bid_ask")

    def test_bid_ask_mixed_multiposition(self):
        positions = [
            Position(side="buy", volume=0.3, entry_price=51100.0),
            Position(side="sell", volume=0.5, entry_price=51050.0),
            Position(side="buy", volume=0.2, entry_price=51230.0),
        ]
        self._check(positions, "bid_ask")
