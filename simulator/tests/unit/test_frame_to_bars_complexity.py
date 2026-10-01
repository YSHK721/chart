"""DataFrame → Bar 列の変換の計算量検定（ISSUE-551 段 2・絶対命令 2026-08-28）。

なぜ在るか（実測 2026-09-28）: `frame_to_bars` は 1 行ごとに `df["列"].iat[i]` で 7 列を
取り出していた。列の取り出しは行数 × 列数回発行され、1 回ごとに Series を作って捨てる。
10 万行で 10.4 秒・全 215 万行で推定 3〜4 分かかり、その間 sim の画面は「準備中」のまま
だった。出力（Bar 列）は同じなので状態検証では原理的に落ちない。

観測の境界: `frame_to_bars` の入力（DataFrame）そのものを Test Spy で包み、列の取り出し
（``df[列]``）の発行を数える（内部名の monkeypatch はしない・絶対命令 2026-09-25）。

表明:
    1. 発行した列の取り出し − 使った列の数 = 0（列ごとに 1 回）。
    2. 行数 10 / 100 の 2 点で発行が増えない。回数そのものは焼き込まない。
"""
from __future__ import annotations

import pandas as pd
import pytest

from simulator.adapter.repository import ohlc_csv, ohlc_marketdata_csv, ohlc_mt5_csv
from simulator.adapter.repository._ohlc_frame import frame_to_bars

_EPOCH_2024_01_01 = 1_704_067_200


class _ColumnSpy:
    """DataFrame の列の取り出し（``df[列]``）を数える Test Spy。"""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self.lookups: "list[str]" = []

    @property
    def columns(self):
        return self._frame.columns

    def __len__(self) -> int:
        return len(self._frame)

    def __getitem__(self, key):
        self.lookups.append(key)
        return self._frame[key]


def _prices(rows: int) -> "dict[str, list]":
    base = [100.0 + i for i in range(rows)]
    return {
        "open": base,
        "high": [b + 0.5 for b in base],
        "low": [b - 0.5 for b in base],
        "close": [b + 0.2 for b in base],
    }


def _comma(rows: int) -> pd.DataFrame:
    return pd.DataFrame({
        "time": [_EPOCH_2024_01_01 + 60 * i for i in range(rows)], **_prices(rows),
        "volume": [1.0] * rows, "spread": [3] * rows,
    })


def _mt5(rows: int) -> pd.DataFrame:
    p = _prices(rows)
    return pd.DataFrame({
        "<DATE>": ["2024.01.01"] * rows,
        "<TIME>": [f"{i // 60:02d}:{i % 60:02d}:00" for i in range(rows)],
        "<OPEN>": p["open"], "<HIGH>": p["high"], "<LOW>": p["low"], "<CLOSE>": p["close"],
        "<TICKVOL>": [1] * rows, "<SPREAD>": [3] * rows,
    })


def _marketdata(rows: int, *, spread: bool) -> pd.DataFrame:
    frame = pd.DataFrame({
        "date": [f"2024-01-01 {i // 60:02d}:{i % 60:02d}:00" for i in range(rows)],
        **_prices(rows), "volume": [1.0] * rows,
    })
    if spread:
        frame["spread"] = [3] * rows
    times = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None)
    return frame.assign(**{ohlc_marketdata_csv._TIME_COLUMN: times})


_FORMS = {
    "comma": (_comma, ohlc_csv.COMMA_SPEC),
    "mt5": (_mt5, ohlc_mt5_csv._SPEC),
    "marketdata": (lambda rows: _marketdata(rows, spread=False), ohlc_marketdata_csv._SPEC),
    "marketdata_spread": (
        lambda rows: _marketdata(rows, spread=True), ohlc_marketdata_csv._SPEC_WITH_SPREAD
    ),
}


def _lookups(form: str, rows: int) -> "list[str]":
    make, spec = _FORMS[form]
    spy = _ColumnSpy(make(rows))
    bars = frame_to_bars(spy, spec)
    assert len(bars) == rows
    return spy.lookups


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_each_column_is_taken_once(form) -> None:
    # Act
    lookups = _lookups(form, rows=10)

    # Assert: 発行 − 使った列 = 0
    assert len(lookups) - len(set(lookups)) == 0, lookups


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_column_lookups_do_not_grow_with_rows(form) -> None:
    # Act / Assert: 行数 10 と 100 で発行が同じ
    assert len(_lookups(form, rows=100)) - len(_lookups(form, rows=10)) == 0
