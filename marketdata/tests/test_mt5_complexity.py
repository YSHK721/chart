"""計算量検定（Test Spy・発行 − 使用 = 0）— ISSUE-447 段階 1 / CX-a〜CX-f。

なぜ状態検証では足りないのか（絶対命令 2026-08-28 の根拠）:
    「作ってから捨てる」欠陥は**出力が正しいまま**なので、出力を見る検定では原理的に落ちない。
    ISSUE-450 では既存 1,233 件が緑のまま 20 日間この浪費を保護し、1m チャートで 1 ティック
    あたり 12.2 秒・破棄率 98.0% を生んでいた。しかも当時の検定は「窓外バーの計算が発行される
    こと」を assert しており、**浪費を仕様へ昇格**させていた。

本ファイルの書き方（規約）:
    測るのは時間ではなく**回数**である（時間の閾値はマシン負荷で揺れ、緩んで浪費を通す）。
    そして**回数そのものを期待値に焼き込まない**。固定するのは「無駄の不在」＝
    ``発行した計算 − 出力に使った計算 = 0`` と、入力を変えた 2 点以上での「増加なし／比例」で
    ある。実装詳細（N 回呼ばれること）は固定しない。
"""
from __future__ import annotations

import datetime as dt

import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import fakes, ingest, journal, m1_chain, usecases
from marketdata.mt5_ticks.cursor import Cursor, CursorContractError
from marketdata.mt5_ticks.fakes import CallSpy, FakeTickSource, FixedClock
from marketdata.mt5_ticks.port import Mt5SupplyError, SupplyUnavailable
from marketdata.mt5_ticks.wire import WireError

_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_REF = "jp225_mt5"
_DAY = dt.date(2026, 8, 25)


def _label_ms(utc: dt.datetime) -> int:
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + 3 * 3600 * 1000


_BASE = _label_ms(dt.datetime(2026, 8, 25, 9, 0))


def _row(index: int, *, ticks_per_ms: int = 1):
    """``index`` 番目のティック。``ticks_per_ms`` 本ごとに ms が 1 進む。"""
    return (_BASE + index // ticks_per_ms, 66000.0 + index * 0.01, 66010.0 + index * 0.01)


def _poll_for(source, tmp_path):
    return usecases.PollOnce(source=source, symbol="JP225", token=_TOKEN, data_dir=tmp_path)


def _run_session(tmp_path, *, polls: int, ticks_per_poll: int, ticks_per_ms: int = 1):
    """``polls`` 回の周期を回し、受信・保存・境界重複を集計する。"""
    source = FakeTickSource([])
    poll = _poll_for(source, tmp_path)
    cursor = Cursor(cursor_ms=_BASE, boundary_rows=())
    produced = 0
    received = appended = 0
    per_poll_dropped = []
    for _ in range(polls):
        source.tape.extend(
            _row(produced + i, ticks_per_ms=ticks_per_ms) for i in range(ticks_per_poll)
        )
        produced += ticks_per_poll
        result = poll(cursor)
        cursor = result.cursor
        received += result.received
        appended += result.appended
        per_poll_dropped.append(result.dropped)
    return {
        "received": received,
        "appended": appended,
        "produced": produced,
        "per_poll_dropped": per_poll_dropped,
        "fetches": len(source.calls),
    }


# =====================================================================
# CX-a 受信 − 保存 = 境界 ms の重複だけ
# =====================================================================

@pytest.mark.parametrize("polls", [2, 8])
@pytest.mark.parametrize("ticks_per_poll", [10, 100])
def test_every_received_row_is_either_stored_or_a_boundary_duplicate(
    tmp_path, polls, ticks_per_poll
):
    """CX-a: ``受信 − 保存 = 境界重複``。説明の付かない行が 1 つも無い。"""
    got = _run_session(tmp_path, polls=polls, ticks_per_poll=ticks_per_poll)
    assert got["received"] - got["appended"] == sum(got["per_poll_dropped"])
    # 供給された行はすべて保存される（取りこぼし 0）。
    assert got["appended"] == got["produced"]


@pytest.mark.parametrize("ticks_per_ms", [1, 3])
def test_boundary_duplication_does_not_grow_with_poll_count_or_session_length(
    tmp_path, ticks_per_ms
):
    """CX-a: 境界重複はポーリング回数・セッション長に**比例しない**（2×2 点で増加なし）。

    固定するのは「1 周期あたりの重複の**上限**が入力で増えないこと」であって、重複の回数では
    ない（回数を焼き込むと浪費が仕様へ昇格する）。上限は「1 ms に載るティック数」であり、
    これは窓の下端を含む設計の帰結＝正しさに必要な入力である。実際の重複数は周期の切れ目が
    ms 群のどこに落ちるかで 1〜``ticks_per_ms`` の間を動くが、**その幅は増えない**。
    """
    points = {
        (polls, ticks_per_poll): _run_session(
            tmp_path / f"p{polls}x{ticks_per_poll}",
            polls=polls, ticks_per_poll=ticks_per_poll, ticks_per_ms=ticks_per_ms,
        )
        for polls in (2, 8)
        for ticks_per_poll in (10, 100)
    }

    for (polls, ticks_per_poll), got in points.items():
        peak = max(got["per_poll_dropped"])
        # 上限はポーリング回数にもセッション長にも依存しない。
        assert peak <= ticks_per_ms, (
            f"polls={polls} len={ticks_per_poll}: 1 周期の境界重複 {peak} が"
            f" 1 ms 内ティック数 {ticks_per_ms} を超えました"
        )
        # 総重複は周期数までしか増えない（保存済み量には一切引きずられない）。
        assert sum(got["per_poll_dropped"]) <= polls * ticks_per_ms

    # セッション長を 10 倍にしても 1 周期あたりの上限は変わらない（増加なしの表明）。
    for polls in (2, 8):
        assert max(points[(polls, 100)]["per_poll_dropped"]) <= max(
            ticks_per_ms, max(points[(polls, 10)]["per_poll_dropped"])
        )


# =====================================================================
# CX-b 新着 0 の周期は 1 バイトも書かない
# =====================================================================

def test_a_poll_with_no_new_rows_issues_no_write_at_all(tmp_path, monkeypatch):
    """CX-b: 新着 0 で journal / parquet / M1 / rollup / カーソルの全書込が 0。"""
    # Arrange: 1 周期ぶんだけ流し、以降テープを伸ばさない。
    source = FakeTickSource([_row(i) for i in range(5)])
    poll = _poll_for(source, tmp_path)
    first = poll(Cursor(cursor_ms=_BASE, boundary_rows=()))

    serialize = CallSpy(journal.serialize_rows, measure=lambda rows: len(rows))
    write_parquet = CallSpy(journal.write_parquet_atomically)
    fold = CallSpy(tick_m1.ticks_to_m1)
    monkeypatch.setattr(journal, "serialize_rows", serialize)
    monkeypatch.setattr(journal, "write_parquet_atomically", write_parquet)
    monkeypatch.setattr(tick_m1, "ticks_to_m1", fold)

    clock = FixedClock(dt.datetime(2026, 8, 25, 9, 0, 30, tzinfo=dt.timezone.utc))
    publish = usecases.PublishDataset(ref=_REF, data_dir=tmp_path, clock=clock)

    # Act: 新着なしの周期。
    second = poll(first.cursor)
    published = publish(second.new_rows)

    # Assert
    assert second.appended == 0
    assert serialize.total == 0, "新着 0 なのに直列化が発行されました"
    assert write_parquet.count == 0
    assert fold.count == 0, "新着 0 なのに M1 畳みが発行されました"
    assert published.bars == 0
    assert not tick_m1.m1_csv_path(ref=_REF, data_dir=tmp_path).exists()
    assert second.cursor == first.cursor


def test_finalizing_unchanged_content_issues_no_parquet_write(tmp_path, monkeypatch):
    """CX-b: ``finalize`` の内容一致時も書込 0。"""
    journal.append(_DAY, [_row(i) for i in range(4)], symbol=_TOKEN, data_dir=tmp_path)
    clock = FixedClock(dt.datetime(2026, 8, 26, 1, 0, tzinfo=dt.timezone.utc))
    finalize = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)
    finalize(days=[_DAY])

    write_parquet = CallSpy(journal.write_parquet_atomically)
    monkeypatch.setattr(journal, "write_parquet_atomically", write_parquet)
    assert finalize(days=[_DAY]) == {_DAY: "unchanged"}
    assert write_parquet.count == 0


# =====================================================================
# CX-c 1 周期の fetch 発行数は状態に依存しない
# =====================================================================

@pytest.mark.parametrize("saved_days", [0, 20])
@pytest.mark.parametrize("cursor_offset", [0, 5_000])
def test_one_cycle_issues_the_same_number_of_fetches_regardless_of_state(
    tmp_path, saved_days, cursor_offset
):
    """CX-c: カーソル位置・保存済み日数が増えても 1 周期の発行数は変わらない（2×2 点）。"""
    # Arrange: 過去日のジャーナルを積む（保存済み日数を増やす）。
    for d in range(saved_days):
        day = _DAY - dt.timedelta(days=d + 1)
        when = dt.datetime.combine(day, dt.time(9, 0))
        journal.append(
            day, [(_label_ms(when) + i, 1.0, 2.0) for i in range(3)],
            symbol=_TOKEN, data_dir=tmp_path,
        )
    source = FakeTickSource([
        (_BASE + cursor_offset + i, 66000.0, 66010.0) for i in range(10)
    ])
    poll = _poll_for(source, tmp_path)

    # Act
    poll(Cursor(cursor_ms=_BASE + cursor_offset, boundary_rows=()))

    # Assert: 発行は「1 周期 = 1 回」で、状態に応じて増えない。
    assert len(source.calls) == 1


def test_fetch_count_is_identical_across_all_four_state_points(tmp_path):
    """CX-c: 4 点すべてで発行数が同一（増加なしの表明）。"""
    counts = set()
    for i, (saved_days, offset) in enumerate([(0, 0), (0, 5_000), (20, 0), (20, 5_000)]):
        root = tmp_path / f"p{i}"
        for d in range(saved_days):
            day = _DAY - dt.timedelta(days=d + 1)
            when = dt.datetime.combine(day, dt.time(9, 0))
            journal.append(
                day, [(_label_ms(when) + j, 1.0, 2.0) for j in range(3)],
                symbol=_TOKEN, data_dir=root,
            )
        source = FakeTickSource([(_BASE + offset + j, 66000.0, 66010.0) for j in range(10)])
        _poll_for(source, root)(Cursor(cursor_ms=_BASE + offset, boundary_rows=()))
        counts.add(len(source.calls))
    assert len(counts) == 1, f"状態によって fetch 発行数が変わりました: {counts}"


# =====================================================================
# CX-d 直列化は新着 k に比例し、当日累積に比例しない
# =====================================================================

@pytest.mark.parametrize("accumulated", [1_000, 100_000])
@pytest.mark.parametrize("k", [5, 10])
def test_serialization_is_proportional_to_new_rows_only(tmp_path, monkeypatch, accumulated, k):
    """CX-d: 当日累積 1,000 / 100,000 のどちらでも、直列化行数は新着 ``k`` に等しい。

    ``tools/live_tick_watch.py:392-399``（当日全量 concat → 再直列化）はこの検定で赤になる。
    """
    # Arrange: 当日ぶんを積んでからスパイを張る（積む工程は測定対象外）。
    journal.append(
        _DAY, [_row(i) for i in range(accumulated)], symbol=_TOKEN, data_dir=tmp_path
    )
    cursor_row = _row(accumulated - 1)
    # 端末のテープは保存済みぶんも保持している（窓の下端を含む以上、境界行は必ず返る）。
    source = FakeTickSource([_row(i) for i in range(accumulated + k)])

    serialize = CallSpy(journal.serialize_rows, measure=lambda rows: len(rows))
    monkeypatch.setattr(journal, "serialize_rows", serialize)

    # Act: 新着 k 行だけの 1 周期。
    result = _poll_for(source, tmp_path)(
        Cursor(cursor_ms=cursor_row[0], boundary_rows=(cursor_row,))
    )

    # Assert: 発行した直列化 − 出力に使った行 = 0。
    assert result.appended == k
    assert serialize.total == k, (
        f"新着 {k} 行に対し {serialize.total} 行を直列化しました"
        f"（当日累積 {accumulated} 行に引きずられています）。"
    )


def test_doubling_the_new_rows_doubles_the_serialization(tmp_path, monkeypatch):
    """CX-d: ``k`` を 2 倍にすると直列化も 2 倍（オーダーの表明）。"""
    totals = {}
    for k in (10, 20):
        root = tmp_path / f"k{k}"
        journal.append(_DAY, [_row(i) for i in range(500)], symbol=_TOKEN, data_dir=root)
        source = FakeTickSource([_row(i) for i in range(500 + k)])
        spy = CallSpy(journal.serialize_rows, measure=lambda rows: len(rows))
        monkeypatch.setattr(journal, "serialize_rows", spy)
        cursor_row = _row(499)
        _poll_for(source, root)(Cursor(cursor_ms=cursor_row[0], boundary_rows=(cursor_row,)))
        totals[k] = spy.total
        monkeypatch.undo()
    assert totals[20] == 2 * totals[10]


def test_finalize_is_not_issued_while_the_day_is_still_open(tmp_path, monkeypatch):
    """CX-d: 確定は 1 日 1 回。当日を回している間は 1 回も発行されない。"""
    source = FakeTickSource([])
    poll = _poll_for(source, tmp_path)
    clock = FixedClock(dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc))
    finalize_uc = usecases.FinalizeDay(token=_TOKEN, data_dir=tmp_path, clock=clock)

    spy = CallSpy(journal.finalize)
    monkeypatch.setattr(journal, "finalize", spy)

    cursor = Cursor(cursor_ms=_BASE, boundary_rows=())
    for cycle in range(6):
        source.tape.extend(_row(cycle * 4 + i) for i in range(4))
        cursor = poll(cursor).cursor
        finalize_uc(days=[_DAY])
    assert spy.count == 0, "当日を回している最中に確定が発行されました"


# =====================================================================
# CX-e Fail-Stop 経路では writer が 1 度も呼ばれない
# =====================================================================

def _fail_stop_cases():
    """検定 E-1〜E-8 に対応する失敗の作り方。"""
    good = [_row(i) for i in range(3)]
    return [
        ("E-1 非単調", FakeTickSource([_row(2), _row(0)], ignore_window=True), Mt5SupplyError),
        ("E-2 窓外", FakeTickSource(good, ignore_window=True), Mt5SupplyError),
        ("E-3 ask<bid", FakeTickSource([(_BASE + 1, 2.0, 1.0)]), Mt5SupplyError),
        ("E-4/E-5 転送不整合", fakes.FailingTickSource(WireError("count 不一致")), WireError),
        ("E-7 認証", fakes.FailingTickSource(SupplyUnavailable("401")), SupplyUnavailable),
        ("E-8 端末 None", fakes.FailingTickSource(SupplyUnavailable("502")), SupplyUnavailable),
    ]


@pytest.mark.parametrize("label,source,expected", _fail_stop_cases(), ids=lambda v: getattr(v, "__name__", str(v))[:24])
def test_fail_stop_paths_issue_no_write(tmp_path, monkeypatch, label, source, expected):
    """CX-e: 異常系のどの経路でも全 writer 呼出が 0（部分的な台帳を残さない）。"""
    serialize = CallSpy(journal.serialize_rows, measure=lambda rows: len(rows))
    write_parquet = CallSpy(journal.write_parquet_atomically)
    fold = CallSpy(tick_m1.ticks_to_m1)
    monkeypatch.setattr(journal, "serialize_rows", serialize)
    monkeypatch.setattr(journal, "write_parquet_atomically", write_parquet)
    monkeypatch.setattr(tick_m1, "ticks_to_m1", fold)

    with pytest.raises(expected):
        _poll_for(source, tmp_path)(Cursor(cursor_ms=_BASE + 1, boundary_rows=()))

    assert serialize.count == 0, f"{label}: ジャーナル書込が発行されました"
    assert write_parquet.count == 0, f"{label}: parquet 書込が発行されました"
    assert fold.count == 0, f"{label}: M1 畳みが発行されました"
    assert not journal.journal_path(_DAY, symbol=_TOKEN, data_dir=tmp_path).exists()


def test_a_boundary_mismatch_writes_nothing(tmp_path, monkeypatch):
    """CX-e / E-6: カーソル契約違反でも書込 0。"""
    serialize = CallSpy(journal.serialize_rows, measure=lambda rows: len(rows))
    monkeypatch.setattr(journal, "serialize_rows", serialize)
    source = FakeTickSource([_row(0), _row(1)])
    stale = (_row(0)[0], 1.0, 2.0)  # 値が保存済みと食い違う境界行。
    with pytest.raises(CursorContractError):
        _poll_for(source, tmp_path)(Cursor(cursor_ms=stale[0], boundary_rows=(stale,)))
    assert serialize.count == 0


# =====================================================================
# CX-f M1 畳みへ渡るのは「閉じた分」だけ
# =====================================================================

@pytest.mark.parametrize("closed_minutes,per_minute", [(2, 30), (4, 15)])
def test_only_closed_minute_ticks_reach_the_m1_fold(tmp_path, monkeypatch, closed_minutes, per_minute):
    """CX-f: ``ticks_to_m1`` へ渡る行数 == 閉じた分のティック数（2 点）。

    形成中の分のティックを渡すと、そのぶんが「畳んでから捨てる」計算になる。
    """
    # Arrange: 閉じた分 + 形成中の分。
    start = dt.datetime(2026, 8, 25, 9, 0)
    rows = []
    for m in range(closed_minutes + 1):
        for i in range(per_minute):
            when = start + dt.timedelta(minutes=m, seconds=i * (60.0 / per_minute))
            rows.append((_label_ms(when), 66000.0 + i, 66010.0 + i))
    until_utc = start + dt.timedelta(minutes=closed_minutes)

    # ``**_`` は価格基準（``price_basis``）などの付随引数を受け流すためだけに在る。
    #   測るのは畳みへ渡った**行数**であり、引数の個数ではない（主張は不変）。
    fold = CallSpy(tick_m1.ticks_to_m1, measure=lambda frame, **_: len(frame))
    monkeypatch.setattr(tick_m1, "ticks_to_m1", fold)

    # Act
    got = m1_chain.append_m1_for_closed_minutes(
        rows, ref=_REF, data_dir=tmp_path, until=until_utc.replace(tzinfo=dt.timezone.utc)
    )

    # Assert: 発行した畳み − 出力に使った行 = 0。
    assert got.bars == closed_minutes
    assert fold.total == closed_minutes * per_minute, (
        f"閉じた分 {closed_minutes * per_minute} 行に対し {fold.total} 行を畳みました"
    )
    assert len(got.pending_rows) == per_minute


def test_the_m1_chain_never_falls_back_to_the_whole_day_rebuilders(tmp_path, monkeypatch):
    """CX-f: 当日全量を読み直す既存経路を呼ばない（P5 棄却の施行）。

    ``append_m1_from_ticks`` / ``build_m1_from_ticks`` は最終バー日の parquet を丸ごと
    読み直す。当日の M1 化にこれを使うと当日累積に比例した無駄が復活する。
    """
    append_whole = CallSpy(tick_m1.append_m1_from_ticks)
    build_whole = CallSpy(tick_m1.build_m1_from_ticks)
    monkeypatch.setattr(tick_m1, "append_m1_from_ticks", append_whole)
    monkeypatch.setattr(tick_m1, "build_m1_from_ticks", build_whole)

    start = dt.datetime(2026, 8, 25, 9, 0)
    rows = [
        (_label_ms(start + dt.timedelta(seconds=i)), 66000.0 + i, 66010.0 + i)
        for i in range(120)
    ]
    m1_chain.append_m1_for_closed_minutes(
        rows, ref=_REF, data_dir=tmp_path,
        until=(start + dt.timedelta(minutes=2)).replace(tzinfo=dt.timezone.utc),
    )
    assert append_whole.count == 0 and build_whole.count == 0
