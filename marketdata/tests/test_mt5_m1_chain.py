"""閉じた分だけの M1 追記と rollup 差分更新の検定（ISSUE-447 段階 1 / 検定 M-3）。

なぜ ``tick_m1.append_m1_from_ticks`` を使わないのか（P5 棄却の根拠）:
    ``tick_m1.append_m1_from_ticks``（`marketdata/tick_m1.py`）は最終バー日の**日別 parquet を丸ごと
    読み直して**再集計する。当日はその parquet がまだ存在せず、存在させると 1 周期ごとに
    当日全量を再計算することになる（当日累積に比例＝CX-d 違反）。当日の M1 化は
    「閉じた分の新着ティックだけを畳む」本モジュールが担う。

形成中の分を持ち越す理由:
    1 つの分バーのティックは複数のポーリング周期にまたがって届く。その周期に届いた分だけで
    バーを確定させると、**途中までのバー**が確定値として CSV に入る。よって形成中の分の行は
    確定させず呼び出し側へ返し、次の周期の入力に混ぜる。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import m1_chain

_REF = "jp225_mt5"


def _label_ms(utc: dt.datetime) -> int:
    """UTC 時刻に対応するサーバ時刻ラベル ms（2026-08 は夏＝UTC+3）。"""
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 3 * 3600 * 1000


def _rows_for_minutes(*, start: dt.datetime, minutes: int, per_minute: int, last: int = None):
    """``start``（UTC）から ``minutes`` 分ぶんのティック行を作る。"""
    rows = []
    for m in range(minutes):
        n = per_minute if (last is None or m < minutes - 1) else last
        for i in range(n):
            when = start + dt.timedelta(minutes=m, seconds=i * (60.0 / max(n, 1)))
            rows.append((_label_ms(when), 66000.0 + i * 0.1, 66010.0 + i * 0.1))
    return rows


_START = dt.datetime(2026, 8, 25, 9, 0, 0)


@pytest.fixture()
def store(tmp_path):
    return dict(ref=_REF, data_dir=tmp_path)


# =====================================================================
# 閉じた分だけを確定する
# =====================================================================

def test_only_closed_minutes_are_appended_and_the_forming_minute_is_returned(store):
    """形成中の分は確定せず、呼び出し側へ持ち越すために返る。"""
    # Arrange: UTC 09:00 と 09:01 は満杯、09:02 は途中まで。
    rows = _rows_for_minutes(start=_START, minutes=3, per_minute=6, last=3)
    until = pd.Timestamp("2026-08-25 09:02", tz="UTC")
    # Act
    got = m1_chain.append_m1_for_closed_minutes(rows, until=until, **store)
    # Assert
    assert got.bars == 2
    assert len(got.pending_rows) == 3
    csv = pd.read_csv(tick_m1.m1_csv_path(ref=_REF, data_dir=store["data_dir"]))
    assert list(csv["date"]) == ["2026-08-25 09:00:00", "2026-08-25 09:01:00"]
    assert list(csv["volume"]) == [6.0, 6.0]


def test_carrying_the_pending_rows_forward_produces_a_complete_bar(store):
    """持ち越した行を次の周期に混ぜると、分バーが途中で確定しない。"""
    # Arrange: 周期 1 は 09:00 の前半 3 本だけを見る。
    first = _rows_for_minutes(start=_START, minutes=1, per_minute=3)
    r1 = m1_chain.append_m1_for_closed_minutes(
        first, until=pd.Timestamp("2026-08-25 09:00", tz="UTC"), **store
    )
    assert r1.bars == 0 and len(r1.pending_rows) == 3
    # Act: 周期 2 で 09:00 の後半 + 09:01 の一部が届く。
    second = [
        (_label_ms(_START + dt.timedelta(seconds=45 + i * 5)), 66100.0, 66110.0)
        for i in range(3)
    ]
    r2 = m1_chain.append_m1_for_closed_minutes(
        list(r1.pending_rows) + second,
        until=pd.Timestamp("2026-08-25 09:01", tz="UTC"),
        **store,
    )
    # Assert: 09:00 は 6 本すべてを含んで確定する。
    assert r2.bars == 1
    csv = pd.read_csv(tick_m1.m1_csv_path(ref=_REF, data_dir=store["data_dir"]))
    assert list(csv["volume"]) == [6.0]
    assert csv["open"].iloc[0] == pytest.approx(66005.0)
    assert csv["close"].iloc[0] == pytest.approx(66105.0)


def test_no_rows_writes_nothing(store):
    """CX-b の土台: 新着 0 で CSV を作らない。"""
    got = m1_chain.append_m1_for_closed_minutes(
        [], until=pd.Timestamp("2026-08-25 09:01", tz="UTC"), **store
    )
    assert got.bars == 0
    assert not tick_m1.m1_csv_path(ref=_REF, data_dir=store["data_dir"]).exists()


def test_only_forming_rows_writes_nothing(store):
    rows = _rows_for_minutes(start=_START, minutes=1, per_minute=4)
    got = m1_chain.append_m1_for_closed_minutes(
        rows, until=pd.Timestamp("2026-08-25 09:00", tz="UTC"), **store
    )
    assert got.bars == 0
    assert not tick_m1.m1_csv_path(ref=_REF, data_dir=store["data_dir"]).exists()


def test_the_csv_path_comes_from_the_existing_authority(store, monkeypatch):
    """出力先を自前で組まない（``tick_m1.m1_csv_path`` へ委譲する）。"""
    moved = store["data_dir"] / "moved.csv"
    monkeypatch.setattr(tick_m1, "m1_csv_path", lambda *a, **k: moved)
    rows = _rows_for_minutes(start=_START, minutes=2, per_minute=3)
    m1_chain.append_m1_for_closed_minutes(
        rows, until=pd.Timestamp("2026-08-25 09:01", tz="UTC"), **store
    )
    assert moved.is_file()


# =====================================================================
# M-3 書式は既存権威と 1 バイト一致
# =====================================================================

def test_appended_bytes_match_the_existing_csv_formatter(store):
    """M-3: 追記した本文が ``tick_m1._format_m1_for_csv`` の出力と一致する。"""
    # Arrange
    rows = _rows_for_minutes(start=_START, minutes=2, per_minute=5)
    until = pd.Timestamp("2026-08-25 09:01", tz="UTC")
    # Act
    m1_chain.append_m1_for_closed_minutes(rows, until=until, **store)
    written = tick_m1.m1_csv_path(ref=_REF, data_dir=store["data_dir"]).read_text(
        encoding="utf-8"
    )
    # Assert: 同じ行を既存フォーマッタへ通した結果と本文が一致する。
    from marketdata.mt5_ticks import ingest

    frame = ingest.rows_to_frame([r for r in rows if r[0] < _label_ms(_START + dt.timedelta(minutes=1))])
    expected_body = tick_m1._format_m1_for_csv(tick_m1.ticks_to_m1(frame)).to_csv(
        header=False, index_label="date"
    )
    assert written.endswith(expected_body)


def test_the_header_is_written_once_and_only_once(store):
    """2 回目以降の追記でヘッダが混ざらない。"""
    rows = _rows_for_minutes(start=_START, minutes=2, per_minute=3)
    m1_chain.append_m1_for_closed_minutes(
        rows, until=pd.Timestamp("2026-08-25 09:01", tz="UTC"), **store
    )
    more = _rows_for_minutes(
        start=_START + dt.timedelta(minutes=1), minutes=2, per_minute=3
    )
    m1_chain.append_m1_for_closed_minutes(
        more, until=pd.Timestamp("2026-08-25 09:02", tz="UTC"), **store
    )
    text = tick_m1.m1_csv_path(ref=_REF, data_dir=store["data_dir"]).read_text(encoding="utf-8")
    # di-ok(C2): これは被検査ソースではなく、書き出した M1 CSV（データ）そのものの検査
    assert text.count("date,") == 1


# =====================================================================
# rollup 差分更新
# =====================================================================

def test_rollups_are_written_under_the_ref_directory(store):
    """``rollups/<ref>/`` に ref_prefix=ref で出る（既存 rollup の規約に委譲）。"""
    rows = _rows_for_minutes(start=_START, minutes=6, per_minute=2)
    m1_chain.append_m1_for_closed_minutes(
        rows, until=pd.Timestamp("2026-08-25 09:05", tz="UTC"), **store
    )
    state = m1_chain.update_rollups(**store)
    out_dir = store["data_dir"] / "rollups" / _REF
    assert out_dir.is_dir()
    assert state is not None
    assert any(p.name.startswith(_REF) for p in out_dir.iterdir())


def test_updating_rollups_without_an_m1_csv_is_a_no_op(store):
    """M1 が無いのに rollup を作らない（空の成果物を置かない）。"""
    assert m1_chain.update_rollups(**store) is None
    assert not (store["data_dir"] / "rollups" / _REF).exists()
