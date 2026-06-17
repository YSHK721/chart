"""CsvOHLCRepository: CSV から domain.Bar 列を読み込む入力アダプタ（MarketDataPort 実装）。

DESIGN §3 価格データ形式（列: open/high/low/close/volume/spread・OHLC 整合・時刻昇順）。
OHLCFrame 型は ports.py に未定義のため list[domain.Bar] を返す（usecase Interactor は
RunBacktestRequest.bars として Bar 列を消費する）。

外側（pandas / OS）例外を内側ドメイン例外へ翻訳する（CLEAN_ARCH §6・逆向き＝
フレームワーク例外の内側漏出を禁止）:
    必須列欠損 → MissingBarError
    OHLC 整合違反 → OHLCInvalidError（domain.Bar.__post_init__ が送出）
    時刻昇順違反 → TimeOrderError
    その他 pandas / IO 例外 → DataError

adapter 層は usecase + domain + 技術ドライバ（pandas）のみに依存する。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.domain.bar import Bar
from backtest.domain.exceptions import (
    DataError,
    MissingBarError,
    TimeOrderError,
)
from backtest.usecase.ports import MarketDataPort

_REQUIRED = ("time", "open", "high", "low", "close", "volume", "spread")


class CsvOHLCRepository(MarketDataPort):
    """CSV → list[domain.Bar] へ変換する MarketDataPort 実装。"""

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        try:
            df = pd.read_csv(source_ref)
        except Exception as exc:  # pandas / OSError 等を内側へ翻訳（漏出禁止）
            raise DataError(
                f"CSV の読み込みに失敗しました: {source_ref}",
                context={"source_ref": str(source_ref), "cause": repr(exc)},
            ) from exc

        return _frame_to_bars(df, source_ref)


def _frame_to_bars(df: pd.DataFrame, source_ref: Any) -> list[Bar]:
    """DataFrame を検証して domain.Bar 列へ変換する（CSV / parquet 共通）。"""
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise MissingBarError(
            f"必須列が不足しています: {missing}",
            context={"missing": missing, "columns": list(df.columns)},
        )

    bars: list[Bar] = []
    prev_time = None
    for i, row in enumerate(df.itertuples(index=False)):
        # OHLC 整合違反は domain.Bar が OHLCInvalidError を送出（内側例外・翻訳不要）
        bar = Bar(
            time=getattr(row, "time"),
            open=float(getattr(row, "open")),
            high=float(getattr(row, "high")),
            low=float(getattr(row, "low")),
            close=float(getattr(row, "close")),
            volume=float(getattr(row, "volume")),
            spread=int(getattr(row, "spread")),
        )
        if prev_time is not None and bar.time <= prev_time:
            raise TimeOrderError(
                "時刻が昇順ではありません",
                bar_index=i,
                context={"prev_time": str(prev_time), "time": str(bar.time)},
            )
        prev_time = bar.time
        bars.append(bar)

    return bars
