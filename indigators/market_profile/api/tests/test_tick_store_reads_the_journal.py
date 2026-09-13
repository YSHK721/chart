"""市場プロファイルのティック読取（gateway）が **受信ジャーナルまで読む** ことの結線検定。

ISSUE-512 段階 2。dwell / zp / 形成中 MP は ``mts.MarketdataTickStore.day_files`` と
``mts.MarketdataTickStore.load_window_ticks`` だけからティックを得る（キャッシュ署名も
``mts.MarketdataTickStore.day_files`` 由来）。ここが parquet しか見ないと、MT5 の当日
（ジャーナルのみ）で MP のティック系が空になる。

data/: 実データを読まない（tmp_path のみ）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
import functools

import numpy as np
import pytest

from marketdata import tick_day_source as tds
from marketdata.mt5_ticks import ingest, journal
from market_profile_api.gateway import marketdata_tick_store as mts

_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_DAY = dt.date(2026, 8, 25)
_COLUMNS = ["timestamp", "bidPrice", "askPrice"]


def _label_ms(h: int, mi: int = 0, s: int = 0) -> int:
    """2026-08-25 のサーバラベル ms（夏時間＝UTC+3）。"""
    return int(dt.datetime(2026, 8, 25, h, mi, s, tzinfo=dt.timezone.utc).timestamp() * 1000)


@pytest.fixture(autouse=True)
def _clear():
    tds.clear_journal_cache()
    yield
    tds.clear_journal_cache()


def test_the_store_lists_through_the_day_source():
    """gateway の列挙は読み元を解決する窓口そのものである（下の差し替えが本番の姿を表す）。"""
    # Arrange / Act / Assert
    assert mts.day_tick_files is tds.day_tick_files


def test_the_store_reads_a_journal_only_day(monkeypatch, tmp_path):
    """確定前（ジャーナルのみ）の日のティックを、列挙・読取・窓復号まで通して返す。"""
    # Arrange
    monkeypatch.setattr(mts, "day_tick_files", functools.partial(tds.day_tick_files, data_dir=tmp_path))
    rows = [(_label_ms(12, 0, s), 100.0 + s, 101.0 + s) for s in range(3)]
    journal.append(_DAY, rows, symbol=_TOKEN, data_dir=tmp_path)
    utc = ingest.rows_to_frame(rows)["timestamp"]
    start = int(utc.iloc[0].timestamp())

    # Act
    win = mts.MarketdataTickStore().load_window_ticks(
        _TOKEN, start, start + 60, columns=_COLUMNS, outlier_frac=0.30
    )

    # Assert
    assert list(win.secs) == [start, start + 1, start + 2]
    np.testing.assert_allclose(win.mids, [100.5, 101.5, 102.5])
