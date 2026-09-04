"""ユースケース（1 周期の組み立て）の検定（ISSUE-447 段階 1）。

1 周期は「fetch 1 回 → absorb → ジャーナル追記 →（分が閉じたら）M1/rollup →（日が変わったら）
確定」である。順序そのものが安全性を持つ: **検証はどの書込よりも先**に済ませる。後にすると
Fail-Stop 時に部分的に書かれた台帳が残る（計算量検定 CX-e が書込 0 を固定する）。
"""
from __future__ import annotations

import datetime as dt

import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import cursor as cur
from marketdata.mt5_ticks import fakes, journal, usecases
from marketdata.mt5_ticks.cursor import Cursor
from marketdata.mt5_ticks.port import Mt5SupplyError

_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_REF = "jp225_mt5"
_DAY = dt.date(2026, 8, 25)


def _label_ms(utc: dt.datetime) -> int:
    """UTC → サーバ時刻ラベル ms（2026-08 は夏＝UTC+3）。"""
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 3 * 3600 * 1000


def _tape(start_utc: dt.datetime, n: int, *, step_ms: int = 1000):
    base = _label_ms(start_utc)
    return [(base + i * step_ms, 66000.0 + i * 0.1, 66010.0 + i * 0.1) for i in range(n)]


def _poll(source, tmp_path, **kw):
    return usecases.PollOnce(
        source=source, symbol="JP225", token=_TOKEN, data_dir=tmp_path, **kw
    )


# =====================================================================
# UC-01 PollOnce
# =====================================================================

def test_poll_once_journals_the_new_rows_and_advances_the_cursor(tmp_path):
    """1 周期で新着が台帳に入り、カーソルが最終行を指す。"""
    # Arrange
    tape = _tape(dt.datetime(2026, 8, 25, 9, 0), 5)
    source = fakes.FakeTickSource(tape)
    start = Cursor(cursor_ms=tape[0][0], boundary_rows=())
    # Act
    got = _poll(source, tmp_path)(start)
    # Assert
    assert got.received == 5 and got.appended == 5 and got.dropped == 0
    assert got.cursor.cursor_ms == tape[-1][0]
    assert journal.read_rows(_DAY, symbol=_TOKEN, data_dir=tmp_path) == tape


def test_the_second_poll_only_journals_what_is_actually_new(tmp_path):
    """境界 ms の再取得ぶんは台帳へ二重に入らない。"""
    tape = _tape(dt.datetime(2026, 8, 25, 9, 0), 3)
    source = fakes.FakeTickSource(tape)
    poll = _poll(source, tmp_path)
    first = poll(Cursor(cursor_ms=tape[0][0], boundary_rows=()))
    source.tape.extend(_tape(dt.datetime(2026, 8, 25, 9, 0, 3), 2))
    second = poll(first.cursor)
    assert second.dropped == 1 and second.appended == 2
    assert journal.read_rows(_DAY, symbol=_TOKEN, data_dir=tmp_path) == source.tape


def test_a_poll_with_nothing_new_writes_nothing(tmp_path):
    """新着 0 の周期で file を作らない。"""
    tape = _tape(dt.datetime(2026, 8, 25, 9, 0), 2)
    source = fakes.FakeTickSource(tape)
    poll = _poll(source, tmp_path)
    first = poll(Cursor(cursor_ms=tape[0][0], boundary_rows=()))
    path = journal.journal_path(_DAY, symbol=_TOKEN, data_dir=tmp_path)
    before = path.read_bytes()
    second = poll(first.cursor)
    assert second.appended == 0
    assert path.read_bytes() == before


def test_a_poll_that_crosses_a_utc_day_writes_into_two_journals(tmp_path):
    """B-2 の経路: 1 応答が 2 日へ分かれて追記される。"""
    tape = _tape(dt.datetime(2026, 8, 24, 23, 59, 58), 4)
    source = fakes.FakeTickSource(tape)
    got = _poll(source, tmp_path)(Cursor(cursor_ms=tape[0][0], boundary_rows=()))
    assert got.days == (dt.date(2026, 8, 24), dt.date(2026, 8, 25))
    assert len(journal.read_rows(dt.date(2026, 8, 24), symbol=_TOKEN, data_dir=tmp_path)) == 2
    assert len(journal.read_rows(dt.date(2026, 8, 25), symbol=_TOKEN, data_dir=tmp_path)) == 2


def test_validation_runs_before_any_write(tmp_path):
    """E-2 の経路: 窓外の行が混じった応答は 1 バイトも書かずに止まる。"""
    tape = _tape(dt.datetime(2026, 8, 25, 9, 0), 3)
    source = fakes.FakeTickSource(tape, ignore_window=True)
    with pytest.raises(Mt5SupplyError):
        _poll(source, tmp_path)(Cursor(cursor_ms=tape[1][0], boundary_rows=()))
    assert not journal.journal_path(_DAY, symbol=_TOKEN, data_dir=tmp_path).exists()


def test_a_retryable_source_failure_propagates_without_writing(tmp_path):
    """供給不能はそのまま伝える（0 行として飲み込まない）。"""
    from marketdata.mt5_ticks.port import SupplyUnavailable

    source = fakes.FailingTickSource(SupplyUnavailable("401"))
    with pytest.raises(SupplyUnavailable):
        _poll(source, tmp_path)(Cursor(cursor_ms=1, boundary_rows=()))
    assert not journal.journal_path(_DAY, symbol=_TOKEN, data_dir=tmp_path).exists()


# =====================================================================
# UC-02 FinalizeDay
# =====================================================================

def _seed_day(tmp_path, day, n=3):
    when = dt.datetime.combine(day, dt.time(9, 0))
    journal.append(day, _tape(when, n), symbol=_TOKEN, data_dir=tmp_path)


def test_a_day_is_finalized_once_a_later_day_has_been_observed(tmp_path):
    """確定条件 1: D+1 の行を観測した。"""
    _seed_day(tmp_path, _DAY)
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 23, 0, tzinfo=dt.timezone.utc))
    finalize = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)
    got = finalize(days=[_DAY], latest_observed_day=dt.date(2026, 8, 26))
    assert got == {_DAY: "written"}
    assert tick_m1.day_parquet_path(_DAY, symbol=_TOKEN, data_dir=tmp_path).is_file()


def test_a_day_is_finalized_once_the_grace_period_has_passed(tmp_path):
    """確定条件 2: ``now >= D+1 + 300s``。"""
    _seed_day(tmp_path, _DAY)
    clock = fakes.FixedClock(dt.datetime(2026, 8, 26, 0, 5, 0, tzinfo=dt.timezone.utc))
    finalize = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)
    assert finalize(days=[_DAY]) == {_DAY: "written"}


def test_a_day_is_not_finalized_before_either_condition_holds(tmp_path):
    """条件が揃わないうちは確定しない（当日を何度も parquet 化しない）。"""
    _seed_day(tmp_path, _DAY)
    clock = fakes.FixedClock(dt.datetime(2026, 8, 26, 0, 4, 59, tzinfo=dt.timezone.utc))
    finalize = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)
    assert finalize(days=[_DAY]) == {}
    assert not tick_m1.day_parquet_path(_DAY, symbol=_TOKEN, data_dir=tmp_path).exists()


def test_finalizing_an_already_finalized_day_does_not_rewrite_it(tmp_path):
    """1 UTC 日 1 回。内容一致なら ``unchanged``。"""
    _seed_day(tmp_path, _DAY)
    clock = fakes.FixedClock(dt.datetime(2026, 8, 26, 1, 0, tzinfo=dt.timezone.utc))
    finalize = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)
    assert finalize(days=[_DAY]) == {_DAY: "written"}
    assert finalize(days=[_DAY]) == {_DAY: "unchanged"}


# =====================================================================
# UC-03 PublishDataset
# =====================================================================

def test_publish_appends_closed_minutes_and_carries_the_forming_one(tmp_path):
    rows = _tape(dt.datetime(2026, 8, 25, 9, 0), 90, step_ms=1000)  # 09:00:00〜09:01:29
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 1, 30, tzinfo=dt.timezone.utc))
    publish = usecases.PublishDataset(ref=_REF, data_dir=tmp_path, clock=clock)
    got = publish(rows)
    assert got.bars == 1                     # 09:00 のみ確定・09:01 は形成中
    assert len(got.pending_rows) == 30
    assert tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).is_file()


def test_publish_without_rows_writes_nothing(tmp_path):
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 1, tzinfo=dt.timezone.utc))
    got = usecases.PublishDataset(ref=_REF, data_dir=tmp_path, clock=clock)([])
    assert got.bars == 0
    assert not tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).exists()


# =====================================================================
# UC-04 RestoreCursor
# =====================================================================

def test_restore_reads_the_cursor_back_from_the_journal(tmp_path):
    """復元の唯一経路はジャーナル（設計 §4）。"""
    tape = _tape(dt.datetime(2026, 8, 25, 9, 0), 4)
    journal.append(_DAY, tape, symbol=_TOKEN, data_dir=tmp_path)
    restore = usecases.RestoreCursor(token=_TOKEN, data_dir=tmp_path)
    got = restore(days=[dt.date(2026, 8, 24), _DAY])
    assert got == cur.from_journal_tail(tape)


def test_restore_prefers_the_latest_day_that_has_a_journal(tmp_path):
    _seed_day(tmp_path, dt.date(2026, 8, 24), n=2)
    _seed_day(tmp_path, _DAY, n=2)
    restore = usecases.RestoreCursor(token=_TOKEN, data_dir=tmp_path)
    got = restore(days=[dt.date(2026, 8, 24), _DAY])
    assert got.cursor_ms == journal.read_rows(_DAY, symbol=_TOKEN, data_dir=tmp_path)[-1][0]


def test_restore_without_any_journal_yields_no_cursor(tmp_path):
    """暗黙既定を作らない＝コールドスタートは呼び出し側の明示（``--from``）を要求する。"""
    restore = usecases.RestoreCursor(token=_TOKEN, data_dir=tmp_path)
    assert restore(days=[_DAY]) is None
