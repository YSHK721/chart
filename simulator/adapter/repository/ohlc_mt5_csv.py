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

from typing import Any, Sequence

import numpy as np
import pandas as pd

from simulator.adapter.repository._ohlc_frame import (
    ColumnSpec,
    float_values,
    frame_to_bars,
    int_values,
    read_csv_or_data_error,
)
from simulator.domain.bar import Bar
from simulator.usecase.ports import MarketDataPort

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


def _columns(df: pd.DataFrame) -> "dict[str, Sequence[Any]]":
    """MT5 形式の全行を domain.Bar 引数ごとの値の列へマッピングする。

    MT5 日付 `2025.01.02` を ISO へ正規化し `<DATE>`+`<TIME>` から numpy.datetime64 を
    生成する（`<...>` 列名は Python 識別子に出来ないため列名で直接参照する）。
    """
    return {
        "time": [
            np.datetime64(_iso_time(str(d), str(t)))
            for d, t in zip(df["<DATE>"].tolist(), df["<TIME>"].tolist())
        ],
        "open": float_values(df["<OPEN>"]),
        "high": float_values(df["<HIGH>"]),
        "low": float_values(df["<LOW>"]),
        "close": float_values(df["<CLOSE>"]),
        "volume": float_values(df["<TICKVOL>"]),
        "spread": int_values(df["<SPREAD>"]),
    }


def _iso_time(date: str, time: str) -> str:
    """MT5 の日付 ``2025.01.02`` と時刻を ISO 表記へ（本形式の時刻の唯一の解釈・UTC naive）。"""
    return f"{date.replace('.', '-')}T{time}"


#: 行の時刻を読むのに要る列（`row_epoch_seconds` が読む列の宣言）。
TIME_COLUMNS = ("<DATE>", "<TIME>")


def row_epoch_seconds(df: pd.DataFrame) -> np.ndarray:
    """各行が Bar になったときの時刻を epoch 秒で返す（全行・行の並びのまま・ISSUE-509）。

    Bar と同じ `_iso_time` の文字列を ``datetime64[s]`` として読む（naive＝UTC は
    `simulator.domain.bar_time.epoch_seconds` の datetime64 の解釈と同じ）。
    """
    iso = [_iso_time(str(d), str(t)) for d, t in zip(df["<DATE>"], df["<TIME>"])]
    return np.array(iso, dtype="datetime64[s]").astype(np.int64)


_SPEC = ColumnSpec(required=_REQUIRED, columns=_columns)


class Mt5CsvOHLCRepository(MarketDataPort):
    """MT5 エクスポート CSV → list[domain.Bar] へ変換する MarketDataPort 実装。"""

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> list[Bar]:
        df = read_csv_or_data_error(source_ref, sep="\t")
        return frame_to_bars(df, _SPEC)
