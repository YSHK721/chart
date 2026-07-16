"""hedged margin — 同一サイド複数玉の統合テスト（ISSUE-095 項目2 / ISSUE-094 E1 残タスク）。

ISSUE-094 E1 で RunBacktestInteractor._execute_every_tick に inline されていた実効証拠金
算出（買い計・売り計を required_margin で合算し大きい側を採る hedging 口座の証拠金相殺）を
Account.hedged_margin_level へ移送した。当時の test は buy+sell 各単玉が主で、「同一サイドに
複数玉があるとき、その required_margin が全玉合算される」ことを固定する明示テストが未整備
だった。本テストはその残タスクを埋め、同一サイド複数玉（相異 entry_price）の hedged_margin_level
が inline 版アルゴリズムと byte 一致することを固定する。

参照（inline 版）: buy_m = Σ required_margin(buy 玉), sell_m = Σ required_margin(sell 玉),
実効証拠金 eff = max(buy_m, sell_m), 維持率 = equity / eff * 100（eff==0 のとき ∞）。
required_margin(pos) = volume × contract_size × entry_price ÷ leverage（Position §5.3）。
"""
from __future__ import annotations

import math

import pytest

from simulator.domain.account import Account
from simulator.domain.position import Position

LEVERAGE = 10.0
CONTRACT_SIZE = 10.0  # JP225 相当


def _inline_hedged_margin_level(account: Account, *, leverage: float, contract_size: float) -> float:
    """ISSUE-094 E1 で Account へ移送される前の inline 実効証拠金算出の独立再実装（参照）。

    Account.hedged_margin_level と同一入力・同一演算順で算出し、両者の一致を固定するための
    非トートロジーな参照（メソッド本体を呼ばず、open_positions から直接合算する）。
    """
    buy_m = sum(
        p.required_margin(leverage, contract_size)
        for p in account.open_positions
        if p.side == "buy"
    )
    sell_m = sum(
        p.required_margin(leverage, contract_size)
        for p in account.open_positions
        if p.side == "sell"
    )
    eff = max(buy_m, sell_m)
    return account.equity / eff * 100.0 if eff > 0 else math.inf


class TestHedgedMarginMultiPositionSameSide:
    def test_multiple_buys_aggregate_and_match_inline(self):
        # Arrange: 買い 3 玉（相異 entry_price）＋売り 1 玉。required_margin = vol×cs×entry/lev。
        #   cs=10, lev=10 → required_margin = vol×entry。
        #   buy_m = 1.0×51000 + 0.5×52000 + 0.3×53000 = 51000 + 26000 + 15900 = 92900
        #   sell_m = 0.4×51500 = 20600 → eff = max = 92900。
        positions = [
            Position(side="buy", volume=1.0, entry_price=51000.0),
            Position(side="buy", volume=0.5, entry_price=52000.0),
            Position(side="buy", volume=0.3, entry_price=53000.0),
            Position(side="sell", volume=0.4, entry_price=51500.0),
        ]
        acc = Account(balance=100_000.0, contract_size=CONTRACT_SIZE, open_positions=positions)

        # Act
        level = acc.hedged_margin_level(leverage=LEVERAGE, contract_size=CONTRACT_SIZE)

        # Assert: 手計算値・inline 参照の双方と一致（同一サイド複数玉の全玉合算を実証）。
        expected_buy_m = 1.0 * 51000.0 + 0.5 * 52000.0 + 0.3 * 53000.0  # 92900
        assert expected_buy_m == pytest.approx(92_900.0)
        assert level == pytest.approx(acc.equity / expected_buy_m * 100.0)
        assert level == pytest.approx(
            _inline_hedged_margin_level(acc, leverage=LEVERAGE, contract_size=CONTRACT_SIZE)
        )

    def test_sell_dominant_multiple_sells_uses_sell_total(self):
        # 売り計が買い計を上回る場合、eff=sell_m（複数売り玉の合算）。
        #   sell_m = 2.0×50000 + 1.0×50500 = 100000 + 50500 = 150500
        #   buy_m  = 0.5×50000 = 25000 → eff = 150500。
        positions = [
            Position(side="sell", volume=2.0, entry_price=50000.0),
            Position(side="sell", volume=1.0, entry_price=50500.0),
            Position(side="buy", volume=0.5, entry_price=50000.0),
        ]
        acc = Account(balance=100_000.0, contract_size=CONTRACT_SIZE, open_positions=positions)

        level = acc.hedged_margin_level(leverage=LEVERAGE, contract_size=CONTRACT_SIZE)

        expected_sell_m = 2.0 * 50000.0 + 1.0 * 50500.0  # 150500
        assert level == pytest.approx(acc.equity / expected_sell_m * 100.0)
        assert level == pytest.approx(
            _inline_hedged_margin_level(acc, leverage=LEVERAGE, contract_size=CONTRACT_SIZE)
        )

    def test_same_side_only_no_opposite_leg(self):
        # 反対玉なし（買いのみ複数玉）: sell_m=0 → eff=buy_m。相殺は起きない。
        positions = [
            Position(side="buy", volume=1.0, entry_price=51000.0),
            Position(side="buy", volume=2.0, entry_price=51200.0),
        ]
        acc = Account(balance=100_000.0, contract_size=CONTRACT_SIZE, open_positions=positions)

        level = acc.hedged_margin_level(leverage=LEVERAGE, contract_size=CONTRACT_SIZE)

        expected_buy_m = 1.0 * 51000.0 + 2.0 * 51200.0  # 153400
        assert level == pytest.approx(acc.equity / expected_buy_m * 100.0)
        assert level == pytest.approx(
            _inline_hedged_margin_level(acc, leverage=LEVERAGE, contract_size=CONTRACT_SIZE)
        )

    def test_equity_reflects_floating_pnl_in_level(self):
        # 実効証拠金は複数買い玉合算・維持率は equity（含み損益込み）で算出されることを固定。
        positions = [
            Position(side="buy", volume=1.0, entry_price=51000.0),
            Position(side="buy", volume=0.5, entry_price=52000.0),
        ]
        acc = Account(balance=100_000.0, contract_size=CONTRACT_SIZE, open_positions=positions)
        # 現値 bid=ask=50000 で含み損を反映（買い 1.5 玉が下落）。
        acc.update_floating_pnl_at(bid=50000.0, ask=50000.0)

        level = acc.hedged_margin_level(leverage=LEVERAGE, contract_size=CONTRACT_SIZE)

        expected_buy_m = 1.0 * 51000.0 + 0.5 * 52000.0  # 77000
        assert acc.equity != pytest.approx(100_000.0)  # floating が効いている
        assert level == pytest.approx(acc.equity / expected_buy_m * 100.0)
        assert level == pytest.approx(
            _inline_hedged_margin_level(acc, leverage=LEVERAGE, contract_size=CONTRACT_SIZE)
        )

    def test_no_positions_returns_infinite(self):
        # 境界: 保有ゼロ → eff=0 → ∞（margin_level と同じ扱い）。
        acc = Account(balance=100_000.0, contract_size=CONTRACT_SIZE, open_positions=[])

        level = acc.hedged_margin_level(leverage=LEVERAGE, contract_size=CONTRACT_SIZE)

        assert math.isinf(level)
        assert math.isinf(
            _inline_hedged_margin_level(acc, leverage=LEVERAGE, contract_size=CONTRACT_SIZE)
        )
