"""CandleSource ポート — 市場データ取得の境界（抽象）。

利用側（指標UI ツール・将来の dataset 供給）はこのポートにのみ依存する。具象（Dukascopy /
CSV 等）はこのポートを満たす adapter として差し替える（DIP）。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Protocol, TypedDict, runtime_checkable


class Candle(TypedDict):
    """供給する 1 本の OHLCV。``time`` は解像度非依存の UNIX 秒（整数）。

    ``volume`` は tick volume または出来高（enabler①）。抽出元が値を持たない場合は
    ``0.0``（``cleaning`` / ``dukascopy_source`` が欠落を 0.0 で補う）。
    """

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@runtime_checkable
class CandleSource(Protocol):
    """OHLC candles の取得ポート。

    実装は ``[start, end)`` の candles を ``time`` 昇順の :class:`Candle` list で返す
    （データなしは空 list）。銘柄・足種・気配側などベンダ固有の設定は具象 adapter の
    構築時パラメータに隔離し、ポートの呼び出し面には出さない。
    """

    def fetch_candles(self, start: datetime, end: datetime) -> List[Candle]:
        ...
