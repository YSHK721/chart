"""CsvOHLCRepository: CSV から domain.Bar 列を読み込む入力アダプタ（MarketDataPort 実装）。

DESIGN §3 価格データ形式（列: open/high/low/close/volume/spread・OHLC 整合・時刻昇順）。
OHLCFrame 型は ports.py に未定義のため list[domain.Bar] を返す（usecase Interactor は
RunBacktestRequest.bars として Bar 列を消費する）。

DataFrame→Bar 変換・必須列チェック・時刻昇順チェック・例外翻訳の共通制御フローは
``_ohlc_frame`` に集約し、本実装は comma 形式の列マッピング（ColumnSpec）のみを残す
（CLEAN_ARCH §6・MT5 タブ形式 ohlc_mt5_csv との重複を排除）。

adapter 層は usecase + domain + 技術ドライバ（pandas）のみに依存する。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from simulator.adapter.repository._ohlc_frame import (
    ColumnSpec,
    frame_to_bars,
    read_csv_or_data_error,
)
from simulator.domain.bar import Bar
from simulator.usecase.ports import MarketDataPort

_REQUIRED = ("time", "open", "high", "low", "close", "volume", "spread")


def _extract(df: pd.DataFrame, i: int) -> "dict[str, Any]":
    """comma 形式 1 行を domain.Bar 引数へマッピングする（time はそのまま採用）。"""
    return {
        "time": df["time"].iat[i],
        "open": float(df["open"].iat[i]),
        "high": float(df["high"].iat[i]),
        "low": float(df["low"].iat[i]),
        "close": float(df["close"].iat[i]),
        "volume": float(df["volume"].iat[i]),
        "spread": int(df["spread"].iat[i]),
    }


# comma 形式（time/open/.../spread）の列マッピング。parquet ローダも同形式を共有する。
COMMA_SPEC = ColumnSpec(required=_REQUIRED, extract=_extract)


class CsvOHLCRepository(MarketDataPort):
    """CSV → list[domain.Bar] へ変換する MarketDataPort 実装。"""

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        df = read_csv_or_data_error(source_ref)
        return frame_to_bars(df, COMMA_SPEC)
