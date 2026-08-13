"""Phase 7: RunBacktestInteractor への PositionManager 挿入（B2 bar 経路）の結線テスト。

合成 Bar 列で決定的に検証する:
    * トレーリング ON: SL が到達価格に追随し、追随後の SL で決済される（FR-07）。
    * 部分決済 ON: 到達で部分 exit が trades に増え、残玉が継続する（FR-08）。
    * NullPositionManager 注入＝未注入（pm=None）で同一結果（LSP・golden 等価）。
    * 同一 spec 2 回で同一結果（決定性）。

usecase を直接駆動する（build_interactor 経由でなく Interactor に DI）。point_size=1.0 で
点数＝価格として推論しやすくする。config は bar-mode（tick_model="ohlc_simulate"）。
"""
from __future__ import annotations

import numpy as np

import pytest

from simulator.adapter.position_manager.position_manager import (
    NullPositionManager,
    PositionManager,
)
from simulator.domain.bar import Bar
from simulator.domain.order import Order
from simulator.domain.partial_close_rule import PartialCloseRule
from simulator.domain.trailing_rule import TrailingRule
from simulator.usecase.models import BacktestConfig, SymbolSpec
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest


class _Indicators:
    def get(self, name):
        return None

    def update(self, bar_index):
        pass


class _Strategy:
    def __init__(self, orders_by_bar):
        self._orders_by_bar = orders_by_bar

    def on_init(self, config, indicators):
        pass

    def on_new_bar(self, bar_index, indicators, account):
        return list(self._orders_by_bar.get(bar_index, []))

    def on_position_check(self, position, bar_index, indicators):
        return "hold"


class _TickModel:
    def ticks_of(self, bar, prev_close):
        return [(bar.close, bar.low, bar.high, bar.time)]


def _bar(t, o, h, l, c):
    return Bar(time=np.datetime64(t), open=o, high=h, low=l, close=c, volume=1.0, spread=0)


def _config():
    return BacktestConfig(
        tick_model="ohlc_simulate",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=1,
        legacy_quirks=False,
        return_basis="equity",
    )


def _spec():
    return SymbolSpec(
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=1,
        point_size=1.0,
        leverage=100.0,
    )


def _run(bars, orders_by_bar, position_manager):
    interactor = RunBacktestInteractor(
        strategy=_Strategy(orders_by_bar),
        indicators=_Indicators(),
        tick_model=_TickModel(),
        position_manager=position_manager,
    )
    request = RunBacktestRequest(
        config=_config(), bars=bars, symbol_spec=_spec(), initial_deposit=100_000.0
    )
    return interactor.execute(request)


# --- トレーリング（FR-07） --------------------------------------------------

def _trailing_bars():
    # bar0 買い建て@100・bar1 で high=110（含み益 10）→ SL を 110-3=107 へ追随・
    # bar2 low=104<=107 で追随後 SL 決済（entry 100 → +7 の利益）。
    return [
        _bar("2024-01-01T00:00", 100.0, 100.0, 100.0, 100.0),
        _bar("2024-01-01T00:01", 100.0, 110.0, 100.0, 105.0),
        _bar("2024-01-01T00:02", 105.0, 106.0, 104.0, 105.0),
    ]


def _trailing_orders():
    return {0: [Order(side="buy", kind="market", volume=1.0, price=None, sl=95.0, tp=None)]}


def test_trailing_stop_follows_reachable_price():
    pm = PositionManager(
        trailing_rule=TrailingRule(
            trigger_points=5, distance_points=3, step_points=0, point_size=1.0
        ),
        trailing_granularity="bar",
    )
    result = _run(_trailing_bars(), _trailing_orders(), pm)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == "sl"
    # 追随後の SL（107.0）で決済＝トレーリングが効いた証拠（初期 SL 95 なら未ヒット）。
    assert t.exit_price == pytest.approx(107.0)
    assert t.pnl() == pytest.approx(7.0)


def test_without_trailing_position_survives():
    # トレーリング無し（Null）では初期 SL 95 に触れず bar2 で決済されない。
    result = _run(_trailing_bars(), _trailing_orders(), NullPositionManager())
    assert len(result.trades) == 0


# --- 部分決済（FR-08） ------------------------------------------------------

def _partial_bars():
    # bar0 買い建て@100 vol=0.10・bar1 high=110（含み益 10・trigger 5 到達）→ 0.05 部分決済＠110・
    # bar2 low=90<=95 で残玉 0.05 が初期 SL=95 決済。
    return [
        _bar("2024-01-01T00:00", 100.0, 100.0, 100.0, 100.0),
        _bar("2024-01-01T00:01", 100.0, 110.0, 100.0, 105.0),
        _bar("2024-01-01T00:02", 105.0, 106.0, 90.0, 95.0),
    ]


def _partial_orders():
    return {0: [Order(side="buy", kind="market", volume=0.10, price=None, sl=95.0, tp=None)]}


def _partial_pm():
    return PositionManager(
        partial_close_rule=PartialCloseRule(
            trigger_profit_points=5, close_fraction=0.5, point_size=1.0
        ),
        trailing_granularity="bar",
        volume_step=0.01,
    )


def test_partial_close_adds_exit_and_residual_continues():
    result = _run(_partial_bars(), _partial_orders(), _partial_pm())
    # 部分 exit（0.05）＋残玉決済（0.05）の 2 トレード。
    assert len(result.trades) == 2
    partial, residual = result.trades[0], result.trades[1]
    # 部分 exit: vol=0.05・**トリガー水準**（entry 100 + trigger 5×point 1.0 = 105）で約定
    #   （bar 粒度は部分 TP＝極値 110 でなくトリガー水準でフィル・依頼者裁定 2026-08-13）。
    #   exit_reason は "partial"（full-TP と区別）。
    assert partial.volume == pytest.approx(0.05)
    assert partial.exit_price == pytest.approx(105.0)
    assert partial.exit_reason == "partial"
    # 残玉: vol=0.05・初期 SL 95 を維持して決済（建玉時 SL/TP 引き継ぎ）。
    assert residual.volume == pytest.approx(0.05)
    assert residual.exit_reason == "sl"
    assert residual.exit_price == pytest.approx(95.0)


def test_partial_close_bar_sell_fills_at_trigger_level():
    # sell@100 vol=0.10・bar1 low=90（含み益 10・trigger 5 到達）→ 0.05 を entry−trigger×point
    #   = 100 − 5 = 95 で部分決済（sell 対称・トリガー水準フィル）。
    bars = [
        _bar("2024-01-01T00:00", 100.0, 100.0, 100.0, 100.0),
        _bar("2024-01-01T00:01", 100.0, 100.0, 90.0, 95.0),
    ]
    orders = {0: [Order(side="sell", kind="market", volume=0.10, price=None,
                        sl=105.0, tp=None)]}
    result = _run(bars, orders, _partial_pm())
    assert len(result.trades) == 1
    partial = result.trades[0]
    assert partial.volume == pytest.approx(0.05)
    assert partial.exit_price == pytest.approx(95.0)
    assert partial.exit_reason == "partial"


def test_partial_close_fires_once_not_every_bar():
    # bar1・bar2 とも high=110 で trigger を満たすが、部分決済は 1 回のみ。
    bars = [
        _bar("2024-01-01T00:00", 100.0, 100.0, 100.0, 100.0),
        _bar("2024-01-01T00:01", 100.0, 110.0, 100.0, 105.0),
        _bar("2024-01-01T00:02", 105.0, 110.0, 105.0, 108.0),
    ]
    result = _run(bars, _partial_orders(), _partial_pm())
    # 部分 exit は 1 件のみ（残玉は未決済で run 終了＝trades に出ない）。
    assert len(result.trades) == 1
    assert result.trades[0].volume == pytest.approx(0.05)


# --- Null 等価・決定性 ------------------------------------------------------

def _serialize(result):
    return (
        [(str(t.side), t.volume, str(t.entry_time), str(t.exit_time), t.entry_price,
          t.exit_price, t.exit_reason) for t in result.trades],
        list(result.balance_curve),
        list(result.equity_curve),
    )


def test_null_manager_equals_unset():
    unset = _run(_trailing_bars(), _trailing_orders(), None)
    null = _run(_trailing_bars(), _trailing_orders(), NullPositionManager())
    assert _serialize(unset) == _serialize(null)


def test_determinism_same_spec_twice():
    a = _run(_partial_bars(), _partial_orders(), _partial_pm())
    b = _run(_partial_bars(), _partial_orders(), _partial_pm())
    assert _serialize(a) == _serialize(b)


# --- tick 経路（B4・every-tick）---------------------------------------------

class _ScriptedTickModel:
    """bar_index → tick 列（(price, bid, ask, time)）を返す実ティック相当モデル。"""

    def __init__(self, ticks_by_bar):
        self._ticks_by_bar = ticks_by_bar
        self._i = 0

    def ticks_of(self, bar, prev_close):
        ticks = self._ticks_by_bar.get(self._i, [])
        self._i += 1
        return list(ticks)


def _config_tick():
    return BacktestConfig(
        tick_model="real_ticks",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=1,
        legacy_quirks=False,
        return_basis="equity",
    )


def _run_tick(bars, orders_by_bar, ticks_by_bar, position_manager):
    interactor = RunBacktestInteractor(
        strategy=_Strategy(orders_by_bar),
        indicators=_Indicators(),
        tick_model=_ScriptedTickModel(ticks_by_bar),
        position_manager=position_manager,
    )
    request = RunBacktestRequest(
        config=_config_tick(), bars=bars, symbol_spec=_spec(), initial_deposit=100_000.0
    )
    return interactor.execute(request)


def test_tick_trailing_stop_follows_tick_price():
    bars = [
        _bar("2024-01-01T00:00", 100.0, 100.0, 100.0, 100.0),
        _bar("2024-01-01T00:01", 100.0, 110.0, 100.0, 105.0),
        _bar("2024-01-01T00:02", 105.0, 106.0, 104.0, 105.0),
    ]
    # spread 0（price=bid=ask）。bar1 2 本目 tick=110 で SL を 110-3=107 へ追随・
    # bar2 tick=104<=107 で追随後 SL 決済。
    ticks = {
        0: [(100.0, 100.0, 100.0, bars[0].time)],
        1: [(100.0, 100.0, 100.0, bars[1].time), (110.0, 110.0, 110.0, bars[1].time)],
        2: [(104.0, 104.0, 104.0, bars[2].time)],
    }
    pm = PositionManager(
        trailing_rule=TrailingRule(
            trigger_points=5, distance_points=3, step_points=0, point_size=1.0
        ),
        trailing_granularity="tick",
    )
    result = _run_tick(bars, _trailing_orders(), ticks, pm)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.exit_reason == "sl"
    assert t.exit_price == pytest.approx(107.0)


def test_tick_partial_close_adds_exit():
    bars = [
        _bar("2024-01-01T00:00", 100.0, 100.0, 100.0, 100.0),
        _bar("2024-01-01T00:01", 100.0, 110.0, 100.0, 105.0),
    ]
    ticks = {
        0: [(100.0, 100.0, 100.0, bars[0].time)],
        1: [(110.0, 110.0, 110.0, bars[1].time)],
    }
    result = _run_tick(bars, _partial_orders(), ticks, _partial_pm())
    # tick=110 で 0.05 部分決済（残玉は未決済で run 終了）。tick 粒度は現在価格
    #   （close_price_for=bid/ask=110）でフィル＝忠実（bar のトリガー水準フィルと非対称・裁定）。
    assert len(result.trades) == 1
    assert result.trades[0].volume == pytest.approx(0.05)
    assert result.trades[0].exit_price == pytest.approx(110.0)
    assert result.trades[0].exit_reason == "partial"


def test_tick_null_equals_unset():
    bars = [
        _bar("2024-01-01T00:00", 100.0, 100.0, 100.0, 100.0),
        _bar("2024-01-01T00:01", 100.0, 110.0, 100.0, 105.0),
    ]
    ticks = {
        0: [(100.0, 100.0, 100.0, bars[0].time)],
        1: [(110.0, 110.0, 110.0, bars[1].time)],
    }
    unset = _run_tick(bars, _trailing_orders(), ticks, None)
    null = _run_tick(bars, _trailing_orders(), ticks, NullPositionManager())
    assert _serialize(unset) == _serialize(null)
