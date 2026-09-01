"""受信ジャーナルと日次確定の検定（ISSUE-447 段階 1 / 検定 N-3・N-4・E-9・B-5・M-2）。

なぜジャーナルを挟むのか（P4 棄却の根拠）:
    ``tools/live_tick_watch.py:392-399`` は行が届くたびに**当日全量を concat して再直列化**する。
    受信が進むほど 1 回の書込が重くなり、当日累積に比例した無駄を生む（計算量検定 CX-d 違反の
    実例）。追記専用のジャーナルにすれば 1 周期の書込は ``O(新着)`` に固定でき、確定
    （parquet 化）は 1 UTC 日に 1 回で済む。

すべて ``tmp_path`` 上で行う（既存データへ 1 バイトも書かない）。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import journal

_DAY = dt.date(2026, 8, 25)
_TOKEN = "JP225@OANDA-Japan-MT5-Live"


def _ms(h, mi=0, s=0, ms=0) -> int:
    """2026-08-25（夏・ラベル = UTC+3）のラベル ms。UTC では 3 時間前になる。"""
    base = dt.datetime(2026, 8, 25, h, mi, s, tzinfo=dt.timezone.utc)
    return int(base.timestamp() * 1000) + ms


def _rows(*specs):
    return [(int(m), float(bid), float(ask)) for m, bid, ask in specs]


@pytest.fixture()
def store(tmp_path):
    """``data_dir`` を tmp_path に閉じた保存先。"""
    return dict(symbol=_TOKEN, data_dir=tmp_path)


# =====================================================================
# M-2 パスは既存権威から派生する
# =====================================================================

def test_journal_path_is_derived_from_the_tick_tree_authority(store):
    """M-2: ``day_parquet_path`` の ``.ndjson`` 差し替えである（自前レイアウトを持たない）。"""
    expected = tick_m1.day_parquet_path(_DAY, **store).with_suffix(".ndjson")
    assert journal.journal_path(_DAY, **store) == expected


def test_journal_path_follows_the_authority_when_it_is_monkeypatched(monkeypatch, tmp_path):
    """M-2: 権威を差し替えるとジャーナルの位置も追随する＝派生が 1 箇所である実証。"""
    moved = tmp_path / "elsewhere" / "X.parquet"
    monkeypatch.setattr(tick_m1, "day_parquet_path", lambda *a, **k: moved)
    assert journal.journal_path(_DAY, symbol=_TOKEN, data_dir=tmp_path) == moved.with_suffix(
        ".ndjson"
    )


def test_the_journal_never_lands_in_the_dukascopy_symbol_tree(store, tmp_path):
    """既存 Dukascopy 木（``JP225``）と別のディレクトリに置かれる。"""
    mt5_path = journal.journal_path(_DAY, **store)
    duka_path = tick_m1.day_parquet_path(_DAY, symbol="JP225", data_dir=tmp_path)
    assert mt5_path.name != duka_path.name


# =====================================================================
# 追記（O(新着)）
# =====================================================================

def test_append_writes_one_line_per_row_and_returns_the_count(store):
    n = journal.append(_DAY, _rows((_ms(12), 1.0, 2.0), (_ms(12, 0, 1), 1.1, 2.1)), **store)
    assert n == 2
    text = journal.journal_path(_DAY, **store).read_text(encoding="utf-8")
    assert text.count("\n") == 2


def test_append_only_adds_and_never_rewrites_what_is_already_there(store):
    """追記専用（既存分を読み直して書き戻さない）。"""
    journal.append(_DAY, _rows((_ms(12), 1.0, 2.0)), **store)
    first = journal.journal_path(_DAY, **store).read_bytes()
    journal.append(_DAY, _rows((_ms(13), 1.1, 2.1)), **store)
    second = journal.journal_path(_DAY, **store).read_bytes()
    assert second.startswith(first)


def test_appending_no_rows_creates_no_file(store):
    """新着 0 で file を作らない（CX-b の土台）。"""
    assert journal.append(_DAY, [], **store) == 0
    assert not journal.journal_path(_DAY, **store).exists()


def test_rows_survive_the_round_trip_bit_for_bit(store):
    """価格が丸められない（float の最短往復表現で保存する）。"""
    rows = _rows((_ms(12), 66018.366000001, 66028.685999999))
    journal.append(_DAY, rows, **store)
    assert journal.read_rows(_DAY, **store) == rows


# =====================================================================
# E-9 異常系: 末尾 torn 行
# =====================================================================

def test_a_torn_last_line_is_dropped_instead_of_raising(store):
    """E-9: 末尾 torn → 末尾行を捨てて復元成功（例外にしない）。"""
    # Arrange: 正常 2 行 + 改行で終わっていない書き掛けの 3 行目。
    journal.append(_DAY, _rows((_ms(12), 1.0, 2.0), (_ms(13), 1.1, 2.1)), **store)
    path = journal.journal_path(_DAY, **store)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('[1756100000000,1.2,2')
    # Act
    got = journal.read_rows(_DAY, **store)
    # Assert
    assert got == _rows((_ms(12), 1.0, 2.0), (_ms(13), 1.1, 2.1))


def test_a_corrupt_line_in_the_middle_is_not_silently_skipped(store):
    """末尾以外の破損は捨てない（欠落を黙認すると穴の空いた台帳ができる）。"""
    path = journal.journal_path(_DAY, **store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[1,1.0,2.0]\nbroken\n[3,1.0,2.0]\n', encoding="utf-8")
    with pytest.raises(Exception):
        journal.read_rows(_DAY, **store)


def test_tail_rows_returns_the_rows_on_the_last_millisecond(store):
    """カーソル復元の入力（最終 ms の全行）を返す。"""
    journal.append(
        _DAY, _rows((_ms(12), 1.0, 2.0), (_ms(13), 1.1, 2.1), (_ms(13), 1.2, 2.2)), **store
    )
    tail = journal.tail_rows(_DAY, **store)
    assert tail[-2:] == _rows((_ms(13), 1.1, 2.1), (_ms(13), 1.2, 2.2))


# =====================================================================
# N-3 / N-4 確定（1 UTC 日 1 回）
# =====================================================================

def test_finalize_writes_a_parquet_with_the_day_schema(store):
    """N-3: 応答 → 追記 → 確定 で列・dtype が既存 tick 木と一致する。"""
    # Arrange
    journal.append(_DAY, _rows((_ms(12), 66020.1, 66035.1), (_ms(13), 66021.2, 66036.2)), **store)
    # Act
    status = journal.finalize(_DAY, **store)
    # Assert
    assert status == "written"
    path = tick_m1.day_parquet_path(_DAY, **store)
    df = pd.read_parquet(path, columns=tick_m1._TICK_COLUMNS)
    assert list(df.columns) == tick_m1._TICK_COLUMNS
    assert str(df["timestamp"].dtype) == "datetime64[ms, UTC]"
    assert all(str(df[c].dtype) == "float64" for c in tick_m1._TICK_COLUMNS[1:])
    assert len(df) == 2


def test_finalize_stores_utc_not_the_server_label(store):
    """ラベル 12:00（夏）は UTC 09:00 として確定する。"""
    journal.append(_DAY, _rows((_ms(12), 1.0, 2.0)), **store)
    journal.finalize(_DAY, **store)
    df = pd.read_parquet(tick_m1.day_parquet_path(_DAY, **store))
    assert df["timestamp"].iloc[0] == pd.Timestamp("2026-08-25 09:00:00", tz="UTC")


def test_the_finalized_day_is_listed_by_the_tick_tree_reader(store):
    """N-4: ``day_parquet_files(symbol=token)`` が当該日を列挙する。"""
    journal.append(_DAY, _rows((_ms(12), 1.0, 2.0)), **store)
    journal.finalize(_DAY, **store)
    files = tick_m1.day_parquet_files(_DAY, _DAY, **store)
    assert files == [tick_m1.day_parquet_path(_DAY, **store)]


def test_finalizing_twice_does_not_rewrite_identical_content(store):
    """内容一致なら書かない（CX-b: 新着 0 の周期で書込 0）。"""
    journal.append(_DAY, _rows((_ms(12), 1.0, 2.0)), **store)
    assert journal.finalize(_DAY, **store) == "written"
    before = tick_m1.day_parquet_path(_DAY, **store).read_bytes()
    assert journal.finalize(_DAY, **store) == "unchanged"
    assert tick_m1.day_parquet_path(_DAY, **store).read_bytes() == before


def test_finalize_keeps_the_journal(store):
    """**ジャーナルは消さない**（確定後も受信の一次記録が残る）。"""
    journal.append(_DAY, _rows((_ms(12), 1.0, 2.0)), **store)
    journal.finalize(_DAY, **store)
    assert journal.journal_path(_DAY, **store).is_file()


def test_finalize_leaves_no_temporary_file_behind(store):
    """原子確定（tmp→replace）の残骸を残さない。"""
    journal.append(_DAY, _rows((_ms(12), 1.0, 2.0)), **store)
    journal.finalize(_DAY, **store)
    parent = tick_m1.day_parquet_path(_DAY, **store).parent
    assert [p.name for p in parent.iterdir() if p.name.endswith(".tmp")] == []


# =====================================================================
# B-5 境界: 0 行の UTC 日（週末）
# =====================================================================

def test_a_day_without_ticks_gets_an_empty_marker_and_no_parquet(store):
    """B-5: 週末は ``.empty`` 1 個・parquet 無し。"""
    status = journal.finalize(_DAY, **store)
    assert status == "empty"
    assert tick_m1.day_empty_marker_path(_DAY, **store).is_file()
    assert not tick_m1.day_parquet_path(_DAY, **store).exists()


def test_an_empty_day_is_not_listed_as_a_parquet_day(store):
    journal.finalize(_DAY, **store)
    assert tick_m1.day_parquet_files(_DAY, _DAY, **store) == []
