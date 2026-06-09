"""DukascopyCandleSource — Dukascopy 実装（``dukascopy-python`` を隔離する adapter）。

無料・アカウント不要の Dukascopy ヒストリカルデータを :class:`CandleSource` ポートへ適合させる。
ライブラリ依存（``dukascopy_python``）はこのモジュールに限定し、利用側へ漏らさない。

時刻:
    Dukascopy は UTC。candles の ``time`` は解像度非依存に ``int(pd.Timestamp(v).timestamp())``
    で UNIX 秒へ変換する（pandas3 で ``astype // 10**9`` は誤り）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List

import pandas as pd

import dukascopy_python
from dukascopy_python.instruments import INSTRUMENT_IDX_ASIA_E_N225JAP

from marketdata.port import Candle

# JP225（日経225）= Dukascopy 銘柄 "E_N225Jap"。
JP225 = INSTRUMENT_IDX_ASIA_E_N225JAP

# 足種名 → ライブラリ INTERVAL 定数（利用側の CLI 等が参照する）。
INTERVALS = {
    "day_1": dukascopy_python.INTERVAL_DAY_1,
    "hour_4": dukascopy_python.INTERVAL_HOUR_4,
    "hour_1": dukascopy_python.INTERVAL_HOUR_1,
    "min_30": dukascopy_python.INTERVAL_MIN_30,
    "min_15": dukascopy_python.INTERVAL_MIN_15,
    "min_5": dukascopy_python.INTERVAL_MIN_5,
    "min_1": dukascopy_python.INTERVAL_MIN_1,
}


def _to_candles(df: pd.DataFrame) -> List[Candle]:
    """UTC OHLC DataFrame を candles へ変換する（純粋・time 昇順・同一 time は後勝ち）。"""
    by_time: dict[int, Candle] = {}
    for ts, o, h, low, c in zip(
        df.index, df["open"], df["high"], df["low"], df["close"]
    ):
        t = int(pd.Timestamp(ts).timestamp())
        by_time[t] = {
            "time": t,
            "open": float(o),
            "high": float(h),
            "low": float(low),
            "close": float(c),
        }
    return [by_time[t] for t in sorted(by_time)]


class DukascopyCandleSource:
    """Dukascopy から OHLC candles を取得する :class:`CandleSource` 実装。

    銘柄・足種・気配側は構築時に固定し、:meth:`fetch_candles` は期間のみを受ける
    （ポートの呼び出し面にベンダ固有を出さない）。クリーニング（外れ値補正）は
    ``marketdata.repair_ohlc_outliers`` として呼び出し側で明示的に合成する（観測可能に保つ）。
    """

    def __init__(
        self,
        *,
        instrument: str = JP225,
        interval: Any = dukascopy_python.INTERVAL_DAY_1,
        offer_side: Any = dukascopy_python.OFFER_SIDE_BID,
    ) -> None:
        self._instrument = instrument
        self._interval = interval
        self._offer_side = offer_side

    def fetch_candles(self, start: datetime, end: datetime) -> List[Candle]:
        """``[start, end)`` の candles を time 昇順で返す（データなしは空 list）。"""
        df = dukascopy_python.fetch(
            self._instrument, self._interval, self._offer_side, start, end
        )
        if df is None or df.empty:
            return []
        return _to_candles(df)
