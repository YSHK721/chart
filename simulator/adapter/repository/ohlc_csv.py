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

from typing import Any, Sequence

import numpy as np
import pandas as pd

from simulator.adapter.repository._ohlc_frame import (
    ColumnSpec,
    cell_values,
    float_values,
    frame_to_bars,
    int_values,
    read_csv_or_data_error,
)
from simulator.domain.bar import Bar
from simulator.domain.bar_time import epoch_seconds
from simulator.usecase.ports import MarketDataPort

_REQUIRED = ("time", "open", "high", "low", "close", "volume", "spread")


def _columns(df: pd.DataFrame) -> "dict[str, Sequence[Any]]":
    """comma 形式の全行を domain.Bar 引数ごとの値の列へマッピングする（time はそのまま採用）。"""
    return {
        "time": cell_values(df["time"]),
        "open": float_values(df["open"]),
        "high": float_values(df["high"]),
        "low": float_values(df["low"]),
        "close": float_values(df["close"]),
        "volume": float_values(df["volume"]),
        "spread": int_values(df["spread"]),
    }


#: 行の時刻を読むのに要る列（`row_epoch_seconds` が読む列の宣言）。
TIME_COLUMNS = ("time",)


def row_epoch_seconds(df: pd.DataFrame) -> np.ndarray:
    """各行が Bar になったときの時刻を epoch 秒で返す（全行・行の並びのまま・ISSUE-509）。

    本形式は 「`time`」 列の値をそのまま Bar.time にする（`_columns`）ので、その値を
    Bar と同じ正規化（`epoch_seconds`）に通す。
    """
    return np.array([epoch_seconds(v) for v in df["time"]], dtype=np.int64)


# comma 形式（time/open/.../spread）の列マッピング。parquet ローダも同形式を共有する。
COMMA_SPEC = ColumnSpec(required=_REQUIRED, columns=_columns)


class CsvOHLCRepository(MarketDataPort):
    """CSV → list[domain.Bar] へ変換する MarketDataPort 実装。"""

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        df = read_csv_or_data_error(source_ref)
        return frame_to_bars(df, COMMA_SPEC)
