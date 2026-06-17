"""E-TradeRecord の単体テスト（CLEAN_ARCH §4 / METRICS §5.2 / PROCESS §6）。

不変条件: exit_time >= entry_time, exit_reason in {sl, tp, reverse, expire}。
公開振る舞い:
    pnl() -> float     METRICS §5.2: (exit - entry) * sign * lot * contract_size + swap + commission
    is_win() -> bool   pnl > 0（同値は非勝ち）
    is_long() -> bool  side == buy
"""
from __future__ import annotations

import numpy as np
import pytest

from backtest.domain.trade_record import TradeRecord
from backtest.domain.exceptions import TimeOrderError, DataError, ExecutionError


def _rec(**kw):
    base = dict(
        side="buy",
        volume=1.0,
        entry_time=np.datetime64("2024-01-01T00:00"),
        exit_time=np.datetime64("2024-01-01T01:00"),
        entry_price=100.0,
        exit_price=110.0,
        contract_size=100_000,
        swap=0.0,
        commission=0.0,
        exit_reason="tp",
    )
    base.update(kw)
    return TradeRecord(**base)


class TestPnl:
    def test_buy_pnl_formula(self):
        # (110 - 100) * +1 * 1 * 100000 + 0 + 0
        assert _rec(side="buy", exit_price=110.0).pnl() == pytest.approx(1_000_000.0)

    def test_sell_pnl_formula(self):
        # (90 - 100) * -1 * 1 * 100000 = +1_000_000
        assert _rec(side="sell", exit_price=90.0).pnl() == pytest.approx(1_000_000.0)

    def test_swap_commission_included(self):
        # price 不変 -> pnl = swap + commission
        assert _rec(exit_price=100.0, swap=-3.0, commission=-2.0).pnl() == pytest.approx(-5.0)


class TestIsWin:
    def test_positive_pnl_is_win(self):
        assert _rec(side="buy", exit_price=110.0).is_win() is True

    def test_zero_pnl_is_not_win(self):
        # 境界: pnl == 0 は非勝ち
        assert _rec(exit_price=100.0).is_win() is False

    def test_negative_pnl_is_not_win(self):
        assert _rec(side="buy", exit_price=90.0).is_win() is False


class TestIsLong:
    def test_buy_is_long(self):
        assert _rec(side="buy").is_long() is True

    def test_sell_is_not_long(self):
        assert _rec(side="sell").is_long() is False


class TestInvariants:
    def test_exit_time_equal_entry_time_is_valid(self):
        # 境界: exit_time == entry_time
        t = np.datetime64("2024-01-01T00:00")
        rec = _rec(entry_time=t, exit_time=t)
        assert rec.exit_time == t

    def test_exit_before_entry_raises(self):
        with pytest.raises(TimeOrderError):
            _rec(
                entry_time=np.datetime64("2024-01-01T02:00"),
                exit_time=np.datetime64("2024-01-01T01:00"),
            )

    def test_invalid_exit_reason_raises(self):
        with pytest.raises(DataError):
            _rec(exit_reason="manual")

    @pytest.mark.parametrize("reason", ["sl", "tp", "reverse", "expire"])
    def test_all_valid_exit_reasons_accepted(self, reason):
        rec = _rec(exit_reason=reason)
        assert rec.exit_reason == reason

    @pytest.mark.parametrize("bad_side", ["BUY", "long"])
    def test_invalid_side_raises_on_pnl(self, bad_side):
        # 回帰（🟡-3）: 不正 side を sell(-1) 扱いし損益符号を静かに反転させない
        with pytest.raises(ExecutionError):
            _rec(side=bad_side).pnl()
