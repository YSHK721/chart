"""MarketDataSourceRepository — marketdata.CandleSource へ委譲し Candle→domain.Bar 写像する
MarketDataPort 実装（S5・strangler 委譲経路）。

設計正典: MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md §2.3（Candle→Bar 写像規則）/ §3.3（class/
method 図・昇順ガード）/ §10.1 C-2（source_ref=(start,end) 半開）/ §10.2 H-4（spread=0 は
spread 非依存戦略のみ＝既定 TC・WeeklyVolBand）。

クリーンアーキ依存方向（厳守）: 本 adapter は usecase（MarketDataPort abc）＋domain（Bar /
例外）＋marketdata（CandleSource 境界ポート）に依存する。**simulator usecase は本 adapter を
import しない**（DIP: usecase は MarketDataPort abc にのみ依存）。Candle→Bar 写像と昇順／OHLC
検証（domain.Bar.__post_init__）を本 adapter に閉じる。

委譲範囲（H-4）: spread 非依存戦略（comma 形式・既定 TC・WeeklyVolBand）に限定する。spread
依存戦略（MA_Slope / MA_Slope_Pending / StopEntryProbe）は委譲対象外＝既存 Mt5CsvOHLCRepository
を維持する（composition root が ea_name 別に振り分ける）。
"""
from __future__ import annotations

from typing import Any

from marketdata import CandleSource
from simulator.domain.bar import Bar
from simulator.domain.exceptions import TimeOrderError
from simulator.usecase.ports import MarketDataPort


def _candles_to_bars(candles: Any) -> list[Bar]:
    """Candle 列を domain.Bar 列へ写像する（§2.3 規則・昇順／OHLC 検証つき）。

    time は int 直渡し、open/high/low/close は float 化、volume は ``c.get("volume", 0.0)``、
    spread は ``0`` 既定（H-4: spread 非依存戦略のみ）。OHLC 整合は ``Bar.__post_init__`` が、
    時刻昇順は本関数のガードが検証する（``frame_to_bars`` と同一の検証点）。
    """
    bars: list[Bar] = []
    prev_time = None
    for c in candles:
        # OHLC 整合違反は domain.Bar が OHLCInvalidError を送出（内側例外・翻訳不要）。
        bar = Bar(
            time=c["time"],
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c.get("volume", 0.0)),
            spread=0,
        )
        if prev_time is not None and bar.time <= prev_time:
            raise TimeOrderError(
                "時刻が昇順ではありません",
                bar_index=len(bars),
                context={"prev_time": str(prev_time), "time": str(bar.time)},
            )
        prev_time = bar.time
        bars.append(bar)
    return bars


class MarketDataSourceRepository(MarketDataPort):
    """marketdata.CandleSource へ委譲し Candle→domain.Bar 写像する MarketDataPort 実装。"""

    def __init__(self, source: CandleSource) -> None:
        self._source = source  # DI: 構築時に CandleSource を注入

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        """``source_ref=(start, end)``（半開・C-2）を fetch_candles へ委譲し Bar 列へ写像する。"""
        start, end = source_ref
        candles = self._source.fetch_candles(start, end)
        return _candles_to_bars(candles)
