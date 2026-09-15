"""テスト期間終了時の清算（`OrderExecutor.close_all_at_final_bar`）の計算量検定（ISSUE-519 G5）。

固定する仕様:
    期末清算の決済価格は**玉のサイドだけで決まる**（買い玉=Bid=最終足 close /
    売り玉=Ask=close+spread×point）。したがって決済価格の解決（`close_price_for`）は
    「期末に残っていたサイドの数」だけ発行されればよく、玉の数にも run の足本数にも
    比例してはならない。

なぜ計算量で固定するか（プロジェクト絶対命令 2026-08-28）:
    玉ごとに `close_price_for` を引き直す実装（N+1）でも、同じサイドの玉には同じ価格が
    配られるので**出力は 1 ビットも変わらない**。状態検証では原理的に落ちない。
    測るのは時間ではなく回数。Test Spy で発行を数え「発行 − 使用 = 0」を表明し、
    玉数・足本数を変えた 2 点以上で発行が増えないことを固定する。回数そのものは
    期待値に焼き込まない（「使用」は出力＝end_of_test トレードのサイド集合から導く）。

Spy の束縛先:
    `order_execution` モジュールに束縛された `close_price_for` を差し替える
    （先例は test_run_backtest_single_engine.py の _ENGINE_MODULES）。
"""
from __future__ import annotations

import numpy as np
import pytest

from simulator.domain.account import Account
from simulator.domain.bar import Bar
from simulator.domain.order import Order
from simulator.domain.position import Position
from simulator.usecase import order_execution as order_execution_module
from simulator.usecase.models import AccountSpec, BacktestConfig, SymbolSpec
from simulator.usecase.open_trade import OpenTrade
from simulator.usecase.order_execution import OrderExecutor
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest
from simulator.usecase.trade_ledger import TradeLedger

_POINT = 0.00001
_LEVERAGE = 100.0
_CONTRACT = 1.0
_ENTRY = 1.10


def _spec() -> SymbolSpec:
    return SymbolSpec(
        contract_size=_CONTRACT,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=_POINT,
    )


def _final_bar() -> Bar:
    """spread>0 の最終足（Bid≠Ask にしてサイドごとに違う価格が配られることを見分ける）。"""
    return Bar(
        time=np.datetime64("2024-01-01T00:05"),
        open=1.10, high=1.11, low=1.09, close=1.10, volume=1.0, spread=20,
    )


def _holding(sides):
    """`sides` の順に 1 ロットずつ保有した OrderExecutor・保有列・確定トレード列を返す。"""
    account = Account(balance=10_000.0, contract_size=_CONTRACT)
    trades: list = []
    ledger = TradeLedger(
        account=account,
        contract_size=_CONTRACT,
        leverage=_LEVERAGE,
        trades=trades,
        deals=[],
        balance_curve=[],
        profit_round_digits=None,
    )
    open_trades = []
    for side in sides:
        pos = Position(side=side, volume=1.0, entry_price=_ENTRY)
        account.open_positions.append(pos)
        account.margin += pos.required_margin(_LEVERAGE, _CONTRACT)
        open_trades.append(
            OpenTrade(
                position=pos, sl=None, tp=None,
                entry_time=np.datetime64("2024-01-01T00:00"),
                entry_price=_ENTRY, opened_bar_index=0,
            )
        )
    executor = OrderExecutor(
        account=account, ledger=ledger, spec=_spec(), leverage=_LEVERAGE,
        contract_size=_CONTRACT, entry_price_basis="close", pending_oco=False,
    )
    return executor, open_trades, trades


def _spy_close_price_for(monkeypatch, original):
    """`order_execution` に束縛された `close_price_for` の発行を数える Spy を被せる。"""
    issued: "list[str]" = []

    def _spy(side, **kwargs):
        issued.append(side)
        return original(side, **kwargs)

    monkeypatch.setattr(order_execution_module, "close_price_for", _spy)
    return issued


def _end_of_test_sides(trades) -> "set[str]":
    """出力に使われたサイド＝期末清算（end_of_test）で確定したトレードのサイド集合。"""
    return {t.side for t in trades if t.exit_reason == "end_of_test"}


_ORIGINAL = order_execution_module.close_price_for

#: 玉構成（境界: 0 玉 / 1 玉 / 同サイド 4 玉 / 両サイド混在）。
_HOLDINGS = [
    pytest.param([], id="no-trade"),
    pytest.param(["buy"], id="one-buy"),
    pytest.param(["buy"] * 4, id="four-buys"),
    pytest.param(["sell"] * 4, id="four-sells"),
    pytest.param(["buy", "sell", "buy", "sell"], id="mixed-4"),
]


class TestTheFinalCloseResolvesTheExitQuoteOncePerSide:
    """発行 − 使用 = 0（使用＝期末に残っていたサイドの数）。"""

    @pytest.mark.parametrize("sides", _HOLDINGS)
    def test_issued_exit_quotes_equal_the_sides_left_open(self, sides, monkeypatch):
        # Arrange
        executor, open_trades, trades = _holding(sides)
        issued = _spy_close_price_for(monkeypatch, _ORIGINAL)
        # Act
        remaining = executor.close_all_at_final_bar(open_trades, _final_bar())
        # Assert: 全玉が end_of_test で確定し、保有列は空になる（取りこぼし無し）。
        assert remaining == []
        assert len(trades) == len(sides)
        # Assert: 発行（決済価格の解決）− 使用（end_of_test のサイド数）= 0。
        assert len(issued) - len(_end_of_test_sides(trades)) == 0, (issued, sides)

    @pytest.mark.parametrize("sides", _HOLDINGS)
    def test_each_side_receives_its_own_exit_quote(self, sides, monkeypatch):
        """サイドごとに解決した価格が、そのサイドの玉にだけ配られること（配り違いの否定）。"""
        # Arrange
        executor, open_trades, trades = _holding(sides)
        bar = _final_bar()
        _spy_close_price_for(monkeypatch, _ORIGINAL)
        # Act
        executor.close_all_at_final_bar(open_trades, bar)
        # Assert: 買い玉=Bid=close / 売り玉=Ask=close+spread×point（価格規則は不変）。
        expected = {"buy": bar.close, "sell": bar.close + bar.spread * _POINT}
        assert [t.side for t in trades] == list(sides)  # 走査順＝確定順
        for t in trades:
            assert t.exit_reason == "end_of_test"
            assert t.exit_time == bar.time
            assert t.exit_price == pytest.approx(expected[t.side]), t

    def test_the_issue_count_does_not_grow_with_the_number_of_open_trades(
        self, monkeypatch
    ):
        """玉数 1→4（同サイド）・2→8（両サイド混在）の 2 系列で発行が増えないこと。"""
        measured = {}
        for label, sides in (
            ("buy-1", ["buy"]),
            ("buy-4", ["buy"] * 4),
            ("mixed-2", ["buy", "sell"]),
            ("mixed-8", ["buy", "sell"] * 4),
        ):
            executor, open_trades, trades = _holding(sides)
            issued = _spy_close_price_for(monkeypatch, _ORIGINAL)
            executor.close_all_at_final_bar(open_trades, _final_bar())
            measured[label] = (len(issued), len(trades))
        # Assert: 出力（確定トレード数）は玉数どおり 4 倍に増える（正の対照）。
        assert measured["buy-4"][1] - 4 * measured["buy-1"][1] == 0, measured
        assert measured["mixed-8"][1] - 4 * measured["mixed-2"][1] == 0, measured
        # Assert: 決済価格の解決は玉数に比例しない（サイド数だけで決まる）。
        assert measured["buy-4"][0] - measured["buy-1"][0] == 0, measured
        assert measured["mixed-8"][0] - measured["mixed-2"][0] == 0, measured


# ---- run 経由: 足本数に比例しないこと（オーダーの表明） ----

class _BuysAtBarZero:
    """bar0 で成行買いを `lots` 本出し、以後は発注しない戦略（反対玉が出ない＝reverse 無し）。"""

    def __init__(self, lots):
        self._lots = lots

    def on_init(self, config, indicators):
        return None

    def on_new_bar(self, bar_index, indicators, account):
        if bar_index != 0:
            return []
        return [Order(side="buy", kind="market", volume=1.0, price=None)] * self._lots

    def on_position_check(self, position, bar_index, indicators):
        return "hold"


class _NullIndicators:
    def get(self, name):
        return None

    def update(self, bar_index):
        return None


class _OneTickPerBar:
    def ticks_of(self, bar, prev_close):
        return [(bar.close, bar.low, bar.high, bar.time)]


def _bars(count):
    out = []
    for i in range(count):
        base = 1.10 + i * 0.001
        out.append(
            Bar(
                time=np.datetime64("2024-01-01T00:00") + np.timedelta64(i, "m"),
                open=base, high=base + 0.01, low=base - 0.01, close=base + 0.005,
                volume=1.0, spread=0,
            )
        )
    return out


def _request(bars, tick_model):
    return RunBacktestRequest(
        config=BacktestConfig(
            tick_model=tick_model, spread_model="fixed", sltp_tie="sl",
            fill_delay="next_tick", ohlc_order="auto", session_calendar="none",
            digits=5, legacy_quirks=False, return_basis="equity",
        ),
        bars=bars,
        symbol_spec=_spec(),
        account=AccountSpec(initial_deposit=10_000.0, leverage=_LEVERAGE, stop_out_level=0.0),
    )


class TestTheFinalCloseDoesNotScaleWithTheRun:
    """足本数 6 / 48 × 玉数 1 / 4 で、期末清算の発行が出力（サイド数）だけで決まること。"""

    @pytest.mark.parametrize("tick_model", ["ohlc_simulate", "real_ticks"], ids=["bar", "tick"])
    def test_issued_exit_quotes_track_the_sides_not_the_bars_or_lots(
        self, tick_model, monkeypatch
    ):
        measured = {}
        for bar_count in (6, 48):
            for lots in (1, 4):
                issued = _spy_close_price_for(monkeypatch, _ORIGINAL)
                interactor = RunBacktestInteractor(
                    strategy=_BuysAtBarZero(lots),
                    indicators=_NullIndicators(),
                    tick_model=_OneTickPerBar(),
                )
                result = interactor.execute(_request(_bars(bar_count), tick_model))
                used = _end_of_test_sides(result.trades)
                # 正の対照: 期末清算が実際に起きている（空振りで緑にならない）。
                assert len(issued) >= 1, (bar_count, lots)
                assert len([t for t in result.trades if t.exit_reason == "end_of_test"]) == lots
                # 発行 − 使用 = 0。
                assert len(issued) - len(used) == 0, (bar_count, lots, issued)
                measured[(bar_count, lots)] = len(issued)
        # Assert: 足本数を 8 倍・玉数を 4 倍にしても発行は増えない。
        assert measured[(48, 1)] - measured[(6, 1)] == 0, measured
        assert measured[(48, 4)] - measured[(6, 4)] == 0, measured
        assert measured[(6, 4)] - measured[(6, 1)] == 0, measured
