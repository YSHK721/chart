"""CsvCandleSource — comma 形式 CSV を :class:`CandleSource` ポートへ適合させる adapter。

simulator の comma 形式 OHLC CSV（``time,open,high,low,close,volume[,spread]``）の実体移管先
（設計 §1.1・§3.3）。``MarketDataSourceRepository`` が本 source へ委譲し ``Candle → domain.Bar``
を写像する（委譲経路）。

Candle 契約（§2.1）に従い ``time`` は UNIX 秒 int、``volume`` は列があれば float／無ければ
``0.0``。``fetch_candles(start, end)`` は ``[start, end)``（半開・C-2）で期間フィルタする。

pandas はインフラ境界の技術ドライバとして adapter 内に隔離する（ポート面には出さない）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List

import pandas as pd

from marketdata.port import Candle


class CsvCandleSource:
    """comma 形式 CSV から OHLC candles を取得する :class:`CandleSource` 実装。

    CSV パスは構築時に固定し、:meth:`fetch_candles` は期間のみを受ける（ポートの呼び出し面に
    ファイルパスを出さない＝CandleSource は datetime のみ受ける契約）。
    """

    def __init__(self, csv_path: Any) -> None:
        self._csv_path = csv_path

    def fetch_candles(self, start: datetime, end: datetime) -> List[Candle]:
        """``[start, end)`` の candles を time 昇順で返す（データなしは空 list・半開）。"""
        df = pd.read_csv(self._csv_path)
        has_volume = "volume" in df.columns
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())

        candles: List[Candle] = []
        for i in range(len(df)):
            raw_t = df["time"].iat[i]
            try:
                t = int(raw_t)
            except (TypeError, ValueError) as exc:
                # Candle 契約（§2.1）: time は UNIX 秒 int。ISO 文字列等の非 epoch を委譲経路へ
                # 流すと黙って report.json が乖離するため fail-fast で契約を明示する（暗黙の
                # フォールバックを設けない・dukascopy_source の KeyError 同方針）。
                raise ValueError(
                    "CsvCandleSource: 'time' 列は UNIX 秒 int である必要があります"
                    f"（非 epoch 値を検出: {raw_t!r}・row={i}）。Candle 契約 §2.1。"
                ) from exc
            if t < start_ts or t >= end_ts:  # [start, end) 半開（C-2）
                continue
            v = float(df["volume"].iat[i]) if has_volume else 0.0
            candles.append(
                {
                    "time": t,
                    "open": float(df["open"].iat[i]),
                    "high": float(df["high"].iat[i]),
                    "low": float(df["low"].iat[i]),
                    "close": float(df["close"].iat[i]),
                    "volume": v,
                }
            )
        candles.sort(key=lambda c: c["time"])
        return candles
