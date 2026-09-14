"""Account.hedged_margin_level 単体テスト（ISSUE-094 🔴-1 抽出物の直接検証）。

RunBacktestInteractor._execute_every_tick に inline されていた「hedging 両建て相殺後の
実効証拠金→維持率」算出を Account の口座不変ルールとして抽出したもの。抽出した計算
オブジェクト単体の契約（相殺・大きい側採用・∞ 規約）を byte-identical に固定する。
"""
from __future__ import annotations

import math

import pytest

from simulator.domain.account import Account
from simulator.domain.position import Position


def _acc(balance, positions, floating=0.0):
    return Account(
        balance=balance,
        open_positions=list(positions),
        floating_pnl=floating,
        contract_size=100_000.0,
    )


class TestHedgedMarginLevel:
    def test_no_positions_returns_inf(self):
        acc = _acc(10_000.0, [])
        assert acc.hedged_margin_level(leverage=100.0, contract_size=100_000.0) == math.inf

    def test_offsets_equal_opposite_positions(self):
        # 同量両建て（買い 1 / 売り 1・同 entry）→ buy_m == sell_m → eff = 片側のみ。
        buy = Position(side="buy", volume=1.0, entry_price=100.0)
        sell = Position(side="sell", volume=1.0, entry_price=100.0)
        acc = _acc(10_000.0, [buy, sell], floating=0.0)
        # required_margin(片側) = 1 * 100000 * 100 / 100 = 100_000
        # eff_margin = max(100000, 100000) = 100000（相殺＝和 200000 ではない）
        # equity = balance + floating = 10_000
        expected = 10_000.0 / 100_000.0 * 100.0
        got = acc.hedged_margin_level(leverage=100.0, contract_size=100_000.0)
        assert got == pytest.approx(expected)

    def test_takes_larger_side_when_asymmetric(self):
        # 買い 2 玉 vs 売り 1 玉 → buy 側が大きい → eff = buy 合計。
        b1 = Position(side="buy", volume=1.0, entry_price=100.0)
        b2 = Position(side="buy", volume=1.0, entry_price=100.0)
        s1 = Position(side="sell", volume=1.0, entry_price=100.0)
        acc = _acc(50_000.0, [b1, b2, s1])
        buy_m = (b1.required_margin(100.0, 100_000.0)
                 + b2.required_margin(100.0, 100_000.0))
        sell_m = s1.required_margin(100.0, 100_000.0)
        eff = max(buy_m, sell_m)
        expected = 50_000.0 / eff * 100.0
        got = acc.hedged_margin_level(leverage=100.0, contract_size=100_000.0)
        assert got == pytest.approx(expected)

    def test_only_buy_side(self):
        b1 = Position(side="buy", volume=2.0, entry_price=120.0)
        acc = _acc(30_000.0, [b1], floating=-500.0)
        eff = b1.required_margin(50.0, 100_000.0)
        expected = (30_000.0 - 500.0) / eff * 100.0
        got = acc.hedged_margin_level(leverage=50.0, contract_size=100_000.0)
        assert got == pytest.approx(expected)

    def test_matches_inline_formula_byte_identical(self):
        # 抽出前の inline 式（open_trades 走査）と同一 float 結果であることを固定する。
        positions = [
            Position(side="buy", volume=0.3, entry_price=51234.5),
            Position(side="sell", volume=0.7, entry_price=51230.1),
            Position(side="buy", volume=0.1, entry_price=51240.0),
        ]
        acc = _acc(100_000.0, positions, floating=123.45)
        leverage, cs = 200.0, 100_000.0
        buy_m = sum(p.required_margin(leverage, cs)
                    for p in positions if p.side == "buy")
        sell_m = sum(p.required_margin(leverage, cs)
                     for p in positions if p.side == "sell")
        eff_margin = max(buy_m, sell_m)
        inline = (acc.equity / eff_margin * 100.0
                  if eff_margin > 0 else float("inf"))
        got = acc.hedged_margin_level(leverage=leverage, contract_size=cs)
        assert got == inline  # byte-identical（近似ではなく完全一致）
