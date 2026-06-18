"""Mt5CsvOHLCRepository: MT5 エクスポート形式 CSV から domain.Bar 列を読み込む。

MT5 ストラテジーテスター/履歴エクスポートの形式（タブ区切り・ヘッダ
`<DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>`・
日付 `2025.01.02`・spread は点 int）を list[domain.Bar] へ変換する MarketDataPort 実装。

既存 ``CsvOHLCRepository``（comma 区切り・time/open/.../spread 列）とは形式が
非互換のため別実装とするが、DataFrame→Bar 変換・必須列チェック・時刻昇順チェック・
例外翻訳の共通制御フローは ``_ohlc_frame`` に集約する。本実装は MT5 形式固有の
列マッピング（ColumnSpec）— タブ区切り読み込みと `<DATE>`+`<TIME>` の datetime64
正規化 — のみを残す（CLEAN_ARCH §6）。

列マッピング:
    <DATE>+<TIME> → time（numpy.datetime64・昇順比較可能）
    <OPEN>/<HIGH>/<LOW>/<CLOSE> → open/high/low/close
    <TICKVOL> → volume（MT5 の M1 OHLC モデルが参照する tick volume）
    <SPREAD>  → spread（点 int・current_open fill で open+spread×point に用いる）
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest.adapter.repository._ohlc_frame import (
    ColumnSpec,
    frame_to_bars,
    read_csv_or_data_error,
)
from backtest.domain.bar import Bar
from backtest.usecase.ports import MarketDataPort

# MT5 エクスポートの必須列（タブ区切りヘッダ）。
_REQUIRED = (
    "<DATE>",
    "<TIME>",
    "<OPEN>",
    "<HIGH>",
    "<LOW>",
    "<CLOSE>",
    "<TICKVOL>",
    "<SPREAD>",
)


def _extract(df: pd.DataFrame, i: int) -> "dict[str, Any]":
    """MT5 形式 1 行を domain.Bar 引数へマッピングする。

    MT5 日付 `2025.01.02` を ISO へ正規化し `<DATE>`+`<TIME>` から numpy.datetime64 を
    生成する（`<...>` 列名は Python 識別子に出来ないため列名で直接参照する）。
    """
    date_iso = str(df["<DATE>"].iat[i]).replace(".", "-")
    time_str = str(df["<TIME>"].iat[i])
    return {
        "time": np.datetime64(f"{date_iso}T{time_str}"),
        "open": float(df["<OPEN>"].iat[i]),
        "high": float(df["<HIGH>"].iat[i]),
        "low": float(df["<LOW>"].iat[i]),
        "close": float(df["<CLOSE>"].iat[i]),
        "volume": float(df["<TICKVOL>"].iat[i]),
        "spread": int(df["<SPREAD>"].iat[i]),
    }


_SPEC = ColumnSpec(required=_REQUIRED, extract=_extract)


class Mt5CsvOHLCRepository(MarketDataPort):
    """MT5 エクスポート CSV → list[domain.Bar] へ変換する MarketDataPort 実装。"""

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        df = read_csv_or_data_error(source_ref, sep="\t")
        return frame_to_bars(df, _SPEC)
