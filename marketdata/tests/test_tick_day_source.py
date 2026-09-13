"""日別ティックの読み元の解決と読取（ISSUE-512 段階 2）の検定。

用語（初出定義）:
    日別ティックファイル
        ＝ 1 UTC 日ぶんのティックが在るファイル。確定 parquet（``<token>_ticks.parquet``）か、
          確定前の受信ジャーナル（``<token>_ticks.ndjson``・MT5 だけが持つ）のどちらか。
    読み元の解決
        ＝ 日ごとに「確定 parquet があればそれ、無ければ受信ジャーナル、どちらも無ければその日を
          飛ばす」を選ぶこと。

なぜ必要か（ISSUE-512 実測 2026-09-11）:
    MT5 は当日をジャーナルで受け、日次確定で parquet 化する。読取側が parquet しか見ないと、
    ``jp225_mt5`` を tick=True にしても **当日だけ足内更新と MP ティック系が空になる**。

計算量（絶対命令）: 測るのは時間ではなく回数。
    ジャーナルは 5 秒ごとに追記される（``tools/mt5_tick_watch.py`` の既定周期）。読むたびに
    1 日全体を変換し直すと、出力は完全に正しいまま当日累積に比例した変換を毎回捨てる
    （実測: 124,806 行で 0.54 秒／回。parquet は 0.025 秒）。状態検証では原理的に落ちない。
    固定するのは **無駄の不在**（変換した行 − 新着行 = 0／読んだ日数 − 使った日数 = 0）であり、
    回数そのものではない。いずれも入力の大きさを変えた 2 点で固定する。

すべて ``tmp_path`` 上で行う（既存データへ 1 バイトも書かない）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from marketdata import tick_day_source as tds
from marketdata import tick_m1, tick_tree
from marketdata.mt5_ticks import ingest, journal

_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_DAY = dt.date(2026, 8, 25)
_NEXT = dt.date(2026, 8, 26)


def _ms(day: dt.date, h: int, mi: int = 0, s: int = 0) -> int:
    """``day`` のサーバラベル ms（夏時間＝UTC+3。UTC では 3 時間前）。"""
    base = dt.datetime(day.year, day.month, day.day, h, mi, s, tzinfo=dt.timezone.utc)
    return int(base.timestamp() * 1000)


def _rows(day: dt.date, count: int, *, start_min: int = 0):
    """``day`` の 12:00（ラベル）から 1 秒おきに ``count`` 行。価格は行ごとに変える。"""
    return [
        (_ms(day, 12, start_min) + i * 1000, 100.0 + i, 101.0 + i) for i in range(count)
    ]


@pytest.fixture()
def store(tmp_path):
    """保存先を tmp_path に閉じた引数一式。"""
    return dict(symbol=_TOKEN, data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _fresh_cache():
    tds.clear_journal_cache()
    yield
    tds.clear_journal_cache()


# =====================================================================
# 1. 読み元の解決
# =====================================================================
def test_a_day_with_only_a_journal_resolves_to_the_journal(store):
    """確定前（parquet 無し）の日はジャーナルを返す。これが当日の空白を埋める本体である。"""
    # Arrange
    journal.append(_DAY, _rows(_DAY, 3), **store)

    # Act
    files = tds.day_tick_files(_DAY, _DAY, **store)

    # Assert
    assert files == [journal.journal_path(_DAY, **store)]


def test_a_finalized_day_resolves_to_the_parquet_even_with_its_journal_kept(store):
    """確定済みの日は parquet を返す（確定後もジャーナルは残る＝両方在る）。"""
    # Arrange
    journal.append(_DAY, _rows(_DAY, 3), **store)
    journal.finalize(_DAY, **store)

    # Act
    files = tds.day_tick_files(_DAY, _DAY, **store)

    # Assert
    assert files == [tick_tree.day_parquet_path(_DAY, **store)]


def test_a_day_with_neither_is_skipped(store):
    """どちらも無い日（休場・未取得）は飛ばす（実データの無い日を作らない）。"""
    # Arrange
    journal.append(_NEXT, _rows(_NEXT, 2), **store)

    # Act
    files = tds.day_tick_files(_DAY, _NEXT, **store)

    # Assert
    assert files == [journal.journal_path(_NEXT, **store)]


def test_a_parquet_only_tree_resolves_exactly_as_the_tick_tree_does(tmp_path):
    """ジャーナルを持たない木（Dukascopy）では、従来の列挙と完全に同じ結果になる。

    表示が変わらないことの検定である。欠損日を挟む 3 日の窓で比べる。
    """
    # Arrange
    for day in (_DAY, _DAY + dt.timedelta(days=2)):
        path = tick_tree.day_parquet_path(day, symbol="JP225", data_dir=tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ingest.rows_to_frame(_rows(day, 2)).to_parquet(path, index=False)
    lo, hi = _DAY, _DAY + dt.timedelta(days=2)

    # Act
    got = tds.day_tick_files(lo, hi, symbol="JP225", data_dir=tmp_path)

    # Assert
    assert got == tick_tree.day_parquet_files(lo, hi, symbol="JP225", data_dir=tmp_path)
    assert len(got) == 2


# =====================================================================
# 2. 読取（ジャーナルは確定 parquet と同じ姿で読める）
# =====================================================================
def test_a_journal_reads_as_the_same_frame_its_finalized_parquet_holds(store):
    """ジャーナルから読んだ frame は、同じ日を確定した parquet と一致する（列・dtype・値）。"""
    # Arrange
    journal.append(_DAY, _rows(_DAY, 50), **store)
    from_journal = tds.read_day_ticks(journal.journal_path(_DAY, **store), tick_m1.TICK_COLUMNS)
    journal.finalize(_DAY, **store)

    # Act
    from_parquet = tds.read_day_ticks(
        tick_tree.day_parquet_path(_DAY, **store), tick_m1.TICK_COLUMNS
    )

    # Assert
    pd.testing.assert_frame_equal(
        from_journal.reset_index(drop=True), from_parquet.reset_index(drop=True)
    )


def test_a_torn_last_line_is_not_read_until_it_is_completed(store):
    """改行で終わっていない末尾行は読まない。書き終わった後の読取で拾う（E-9 と同じ規則）。"""
    # Arrange
    path = journal.journal_path(_DAY, **store)
    journal.append(_DAY, _rows(_DAY, 2), **store)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('[1,2.0')                                     # 書き掛け
    first = tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)

    # Act — 書き掛けを完結させる
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(",3.0]\n")
    second = tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)

    # Assert
    assert len(first) == 2
    assert len(second) == 3


def test_reading_after_an_append_returns_every_row_so_far(store):
    """追記の前後で読むと、後の読取は全行を返す（増分で読んでも全量と一致する）。"""
    # Arrange
    path = journal.journal_path(_DAY, **store)
    journal.append(_DAY, _rows(_DAY, 5), **store)
    tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)
    journal.append(_DAY, _rows(_DAY, 4, start_min=10), **store)

    # Act
    got = tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)

    # Assert
    expected = ingest.rows_to_frame(journal.read_rows(_DAY, **store))
    pd.testing.assert_frame_equal(got.reset_index(drop=True), expected.reset_index(drop=True))


def test_a_replaced_journal_is_read_from_the_beginning(store):
    """ジャーナルが別の中身へ置き換わったら、覚えた位置を捨てて先頭から読む（古い姿を配らない）。"""
    # Arrange
    path = journal.journal_path(_DAY, **store)
    journal.append(_DAY, _rows(_DAY, 8), **store)
    tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)
    path.unlink()
    journal.append(_DAY, _rows(_DAY, 3, start_min=30), **store)   # 短い別の中身

    # Act
    got = tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)

    # Assert
    expected = ingest.rows_to_frame(journal.read_rows(_DAY, **store))
    pd.testing.assert_frame_equal(got.reset_index(drop=True), expected.reset_index(drop=True))


def test_the_returned_frame_is_not_the_remembered_one(store):
    """読み手が返り値を書き換えても、覚えている変換済み行は汚れない。"""
    # Arrange
    path = journal.journal_path(_DAY, **store)
    journal.append(_DAY, _rows(_DAY, 3), **store)
    got = tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)

    # Act
    got.loc[:, "bidPrice"] = -1.0
    again = tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)

    # Assert
    assert (again["bidPrice"] > 0).all()


def test_a_finalized_day_drops_its_remembered_journal(store):
    """確定して parquet で読める日は、覚えていたジャーナルを手放す（記憶が日数ぶん増えない）。"""
    # Arrange
    journal.append(_DAY, _rows(_DAY, 3), **store)
    tds.read_day_ticks(journal.journal_path(_DAY, **store), tick_m1.TICK_COLUMNS)
    journal.finalize(_DAY, **store)

    # Act
    tds.day_tick_files(_DAY, _DAY, **store)

    # Assert
    assert tds.remembered_journals() == 0


# =====================================================================
# 3. 形成中バー（集計規則は tick_m1 の 1 箇所）
# =====================================================================
def test_the_forming_bar_from_a_journal_matches_the_one_from_its_parquet(store):
    """同じ窓の形成中バーは、ジャーナルから読んでも確定 parquet から読んでも一致する。"""
    # Arrange
    journal.append(_DAY, _rows(_DAY, 120), **store)
    frame = ingest.rows_to_frame(journal.read_rows(_DAY, **store))
    first = int(frame["timestamp"].iloc[0].timestamp())
    window = (first, first + 60)
    # 比較相手（tick_m1 の parquet 版）は mid 固定なので同じ基準で比べる。
    from_journal = tds.forming_bar_from_ticks(*window, price_basis=tick_m1.PRICE_BASIS_MID, **store)
    journal.finalize(_DAY, **store)

    # Act
    from_parquet = tick_m1.forming_bar_from_ticks(*window, **store)

    # Assert
    assert from_journal is not None
    assert from_journal == from_parquet


def test_a_window_without_ticks_has_no_forming_bar(store):
    """窓にティックが無ければ None（実データの無いバーを作らない）。"""
    # Arrange
    journal.append(_DAY, _rows(_DAY, 3), **store)
    noon = int(dt.datetime(2026, 8, 25, 23, tzinfo=dt.timezone.utc).timestamp())

    # Act / Assert
    assert tds.forming_bar_from_ticks(
        noon, noon + 60, price_basis=tick_m1.PRICE_BASIS_BID, **store
    ) is None


# =====================================================================
# 4. 計算量（回数で固定・回数そのものは焼き込まない）
# =====================================================================
class _ConvertSpy:
    """ジャーナル行 → frame の変換（``ingest.rows_to_frame``）に渡った行数を控える。"""

    def __init__(self, monkeypatch) -> None:
        self.converted = 0
        real = ingest.rows_to_frame

        def counting(rows):
            rows = list(rows)
            self.converted += len(rows)
            return real(rows)

        monkeypatch.setattr(tds.ingest, "rows_to_frame", counting)


@pytest.mark.parametrize("already", [10, 1_000])
def test_a_reread_converts_only_the_rows_appended_since(monkeypatch, store, already):
    """変換した行 − 新着行 = 0（当日の累積 ``already`` を 2 点に変えても成り立つ）。

    読むたびに 1 日全体を変換し直す実装は、出力が正しいまま ``already`` 行を毎回捨てる。
    """
    # Arrange
    spy = _ConvertSpy(monkeypatch)
    path = journal.journal_path(_DAY, **store)
    journal.append(_DAY, _rows(_DAY, already), **store)
    tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)
    new = _rows(_DAY, 7, start_min=40)
    journal.append(_DAY, new, **store)
    spy.converted = 0

    # Act
    got = tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)

    # Assert
    assert len(got) == already + len(new), "検定が空振りしている（全行が返っていない）"
    assert spy.converted - len(new) == 0, (
        f"新着 {len(new)} 行に対し {spy.converted} 行を変換した（当日累積 {already} 行を"
        " 作り直して捨てている）"
    )


@pytest.mark.parametrize("already", [10, 1_000])
def test_an_unchanged_journal_converts_nothing(monkeypatch, store, already):
    """追記が無ければ変換 0 行（読むたびに作り直さない）。"""
    # Arrange
    spy = _ConvertSpy(monkeypatch)
    path = journal.journal_path(_DAY, **store)
    journal.append(_DAY, _rows(_DAY, already), **store)
    tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)
    spy.converted = 0

    # Act
    tds.read_day_ticks(path, tick_m1.TICK_COLUMNS)

    # Assert
    assert spy.converted == 0


@pytest.mark.parametrize("days", [1, 2])
def test_the_forming_bar_reads_only_the_days_it_uses(monkeypatch, store, days):
    """読んだ日数 − 使った日数 = 0（窓が跨ぐ日数を 2 点に変えても成り立つ）。

    「使った日」＝窓 ``[start, end)`` と重なる日。前後の日にもファイルを置き、窓の外を
    読まないことを見る。
    """
    # Arrange
    for day in (_DAY - dt.timedelta(days=1), _DAY, _NEXT, _NEXT + dt.timedelta(days=1)):
        journal.append(day, _rows(day, 3), **store)
    start = int(dt.datetime(2026, 8, 25, 20, tzinfo=dt.timezone.utc).timestamp())
    end = start + (3600 if days == 1 else 6 * 3600)          # 1 日内 / 翌 UTC 日へ跨ぐ
    read = []
    real = tds.read_day_ticks
    monkeypatch.setattr(tds, "read_day_ticks", lambda p, c: (read.append(p), real(p, c))[1])

    # Act
    tds.forming_bar_from_ticks(start, end, price_basis=tick_m1.PRICE_BASIS_BID, **store)

    # Assert
    used = {
        journal.journal_path(d, **store)
        for d in pd.date_range(
            pd.Timestamp(start, unit="s").normalize(),
            pd.Timestamp(end - 1, unit="s").normalize(),
        )
    }
    assert len(used) == days, "検定が空振りしている（窓の跨ぐ日数が想定と違う）"
    assert len(read) - len(used) == 0 and set(read) == used, (
        f"使う {len(used)} 日に対し {len(read)} 日を読んだ: {read}"
    )
