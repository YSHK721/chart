"""dataset.load_candles の一括変換（ISSUE-301）の AAA。

固定する契約:
  1. **同値**: 一括変換の出力は、従来の 1 行ずつ（``iterrows`` + ``_to_unix_seconds``）
     変換したものと完全に一致する（キー順・型・値）。ここが崩れると全 UI の足がずれる。
  2. 時刻の変換規則は ``_to_unix_seconds`` 1 か所のまま（``_index_unix_seconds`` は
     それを一括化しただけ）。tz 付き・tz 無しのどちらでも 1 点ずつと一致する。
  3. DatetimeIndex でない索引でも従来どおり変換できる（フェイルセーフ）。

背景（実測 2026-08-08）: ``load_candles`` はリプレイ再生で毎バー・毎リクエスト呼ばれる共有
ホットパスで、``/market_profile`` 1 回 4.49 秒のうち 4.365 秒（97%）を占めていた。
その大半が ``iterrows``（5 万行で 1.20 秒）だった。
"""
from __future__ import annotations

import pandas as pd
import pytest

from marketdata import dataset


def _row_wise(df):
    """従来実装（比較用の参照）。"""
    lower_map = {str(c).lower(): c for c in df.columns}
    cols = {k: lower_map[k] for k in ("open", "high", "low", "close")}
    out = []
    for idx, row in df.iterrows():
        out.append({
            "time": dataset._to_unix_seconds(idx),
            "open": float(row[cols["open"]]),
            "high": float(row[cols["high"]]),
            "low": float(row[cols["low"]]),
            "close": float(row[cols["close"]]),
        })
    return out


def _frame(index):
    n = len(index)
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [101.5 + i for i in range(n)],
            "Low": [99.25 + i for i in range(n)],
            "Close": [100.75 + i for i in range(n)],
            "Volume": [10 + i for i in range(n)],
        },
        index=index,
    )


NAIVE = pd.date_range("2026-08-06 20:00:00", periods=5, freq="1min")
AWARE = pd.date_range("2026-08-06 20:00:00", periods=5, freq="1min", tz="UTC")


@pytest.mark.parametrize("index", [NAIVE, AWARE], ids=["tz無し", "tz付き"])
def test_load_candles_は1行ずつ変換したものと完全一致する(index, monkeypatch):
    # Arrange
    df = _frame(index)
    monkeypatch.setattr(dataset, "load_dataframe", lambda ref, timeframe=None: df)
    # Act
    got = dataset.load_candles("dummy_ref", "1m")
    # Assert
    assert got == _row_wise(df)


@pytest.mark.parametrize("index", [NAIVE, AWARE], ids=["tz無し", "tz付き"])
def test_時刻の一括変換は1点ずつと一致する(index):
    assert dataset._index_unix_seconds(index) == [dataset._to_unix_seconds(v) for v in index]


def test_DatetimeIndex以外でも1点ずつ変換へ落ちる():
    # Arrange: 文字列索引（datetime64 でない）。
    index = pd.Index(["2026-08-06 20:00:00", "2026-08-06 20:01:00"])
    # Act / Assert
    assert dataset._index_unix_seconds(index) == [dataset._to_unix_seconds(v) for v in index]


def test_limit_は末尾N本のまま(monkeypatch):
    df = _frame(NAIVE)
    monkeypatch.setattr(dataset, "load_dataframe", lambda ref, timeframe=None: df)
    got = dataset.load_candles("dummy_ref", "1m", 2)
    assert got == _row_wise(df.tail(2))
