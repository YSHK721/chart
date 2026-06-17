"""TickModel 実装（TickModelPort・PROCESS §0.2/§7-#1・CLEAN_ARCH §6.3）。

ticks_of(bar, prev_close) -> Iterable[Tick]   # Tick = (price, bid, ask, time)

    OhlcExpandTickModel: 1 バーを O→H→L→C の 4 疑似ティックへ展開（決定論・§7-#5）。
    OpenOnlyTickModel  : 始値のみ（1 ティック）。
    EveryTickModel     : 実ティック列。OHLC のみの入力では O→H→L→C 近似へフォール
                         バック（実ティック供給は将来の Dukascopy gateway＝範囲外）。

最小骨格: spread=0 のとき bid=ask=price（実 spread は spread_model 接続時に拡張）。
Tick は標準 tuple（フレームワーク型を漏らさない）。
"""
from __future__ import annotations

from typing import Any, Iterable

from backtest.usecase.ports import TickModelPort


def _tick(price: float, bar: Any) -> tuple:
    # spread=0 の最小骨格: bid=ask=price。Tick = (price, bid, ask, time)
    half = getattr(bar, "spread", 0) / 2.0
    return (price, price - half, price + half, bar.time)


def _ohlc_ticks(bar: Any) -> Iterable[tuple]:
    for price in (bar.open, bar.high, bar.low, bar.close):
        yield _tick(price, bar)


class OhlcExpandTickModel(TickModelPort):
    """O→H→L→C の 4 疑似ティックへ展開する。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return _ohlc_ticks(bar)


class OpenOnlyTickModel(TickModelPort):
    """始値のみ（1 ティック）。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return [_tick(bar.open, bar)]


class EveryTickModel(TickModelPort):
    """実ティック列。OHLC のみの入力では O→H→L→C 近似へフォールバックする。"""

    def ticks_of(self, bar: Any, prev_close: float) -> Iterable[tuple]:
        return _ohlc_ticks(bar)
