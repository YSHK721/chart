"""adapter/execution/tick_model.py の TickModel テスト（TickModelPort・PROCESS §0.2/§7-#1）。

TickModelPort.ticks_of(bar, prev_close) -> Iterable[Tick]   # Tick = (price, bid, ask, time)

実装（CLEAN_ARCH §6.3）:
    - OhlcExpandTickModel: 1 バーを O→H→L→C の 4 疑似ティックへ展開（決定論）。
    - OpenOnlyTickModel  : 始値のみ（1 ティック）。
    - EveryTickModel     : 実ティック列。OHLC のみの入力では O→H→L→C 近似へフォールバック
      （実ティック未供給時の決定論的近似。Dukascopy 実ティック供給は範囲外＝将来）。

最小骨格: spread=0 のとき bid=ask=price（実 spread は spread_model 接続時に拡張＝範囲外）。
"""
from __future__ import annotations

from backtest.domain.bar import Bar
from backtest.usecase.ports import TickModelPort


def _bar():
    return Bar(time=0, open=1.0, high=1.5, low=0.8, close=1.2, volume=10.0, spread=0)


# --- OhlcExpandTickModel ----------------------------------------------------

def test_ohlc_expand_implements_tick_model_port():
    # Arrange / Act
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    # Assert: LSP
    assert isinstance(OhlcExpandTickModel(), TickModelPort)


def test_ohlc_expand_yields_open_high_low_close_in_order():
    # Arrange: O→H→L→C の 4 疑似ティック（PROCESS §7-#5 ohlc_order）
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    bar = _bar()

    # Act
    ticks = list(OhlcExpandTickModel().ticks_of(bar, prev_close=1.1))

    # Assert: price 列が O→H→L→C
    prices = [t[0] for t in ticks]
    assert prices == [1.0, 1.5, 0.8, 1.2]


def test_ohlc_expand_tick_has_price_bid_ask_time_shape():
    # Arrange: Tick = (price, bid, ask, time)。spread=0 のとき bid=ask=price
    from backtest.adapter.execution.tick_model import OhlcExpandTickModel

    bar = _bar()

    # Act
    first = list(OhlcExpandTickModel().ticks_of(bar, prev_close=1.1))[0]

    # Assert
    assert len(first) == 4
    price, bid, ask, time = first
    assert price == 1.0 and bid == 1.0 and ask == 1.0
    assert time == bar.time


# --- OpenOnlyTickModel ------------------------------------------------------

def test_open_only_yields_single_tick_at_open():
    # Arrange: 始値のみ（1 ティック）
    from backtest.adapter.execution.tick_model import OpenOnlyTickModel

    bar = _bar()

    # Act
    ticks = list(OpenOnlyTickModel().ticks_of(bar, prev_close=1.1))

    # Assert
    assert len(ticks) == 1
    assert ticks[0][0] == 1.0  # price == open


# --- EveryTickModel（OHLC 入力フォールバック）------------------------------

def test_every_tick_falls_back_to_ohlc_expand_without_real_ticks():
    # Arrange: 実ティック未供給時は O→H→L→C 近似（決定論）
    from backtest.adapter.execution.tick_model import EveryTickModel

    bar = _bar()

    # Act
    prices = [t[0] for t in EveryTickModel().ticks_of(bar, prev_close=1.1)]

    # Assert
    assert prices == [1.0, 1.5, 0.8, 1.2]
