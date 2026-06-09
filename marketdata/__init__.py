"""marketdata — 市場データ供給の境界（ポート）と差し替え可能な取得 adapter。

「チャート/指標へ OHLC を取り込む」関心事を、指標（計算）・指標UI（配信）から分離して
ここへ集約する。利用側は :class:`CandleSource` ポート（``fetch_candles(start, end) ->
list[Candle]``）にのみ依存し、ベンダ固有（Dukascopy 等）は背後の adapter に隔離する。

- :class:`Candle`               : 供給する OHLC の形（``{time(UNIX秒), open, high, low, close}``）。
- :class:`CandleSource`         : 取得ポート（抽象・DIP の依存先）。
- :class:`DukascopyCandleSource`: Dukascopy 実装（``dukascopy-python`` を隔離）。
- :func:`repair_ohlc_outliers`  : 足内 OHLC 外れ値の純粋な補正（ベンダ非依存のクリーニング）。

依存方向: 利用側 → CandleSource（抽象）← DukascopyCandleSource（具象）。
"""

from __future__ import annotations

from marketdata.cleaning import repair_ohlc_outliers
from marketdata.dukascopy_source import INTERVALS, JP225, DukascopyCandleSource
from marketdata.port import Candle, CandleSource

__all__ = [
    "Candle",
    "CandleSource",
    "DukascopyCandleSource",
    "INTERVALS",
    "JP225",
    "repair_ohlc_outliers",
]
