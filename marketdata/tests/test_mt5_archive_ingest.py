"""OANDA 月別アーカイブ（月 zip）→ tick 木の取り込み検定（ISSUE-447 段階 2）。

段階 1 の HTTP 増分経路とは**別の供給経路**である。
入力は「MT5 サーバ時刻ラベルの TAB テキスト」であり、出力は既存 tick 木の日別 parquet。
規約（列・dtype・パス・UTC 日境界）は :mod:`marketdata.tick_m1` と
:mod:`marketdata.mt5_ticks.ingest` が権威で、本層は**委譲**する。

実測に基づく前提（2026-09-02・全 76 か月 75,082,747 行を走査）:
    - 全行が ``<DATE>\\t<TIME>\\t<BID>\\t<ASK>\\t<LAST>\\t<VOLUME>``（6 列 TAB）で
      ``<TIME>`` のミリ秒は **3 桁**（75,082,747 / 75,082,747）。
    - 10 月 DST 切替日（多価の 1 時間）に行は **0 行**（2020〜2025 の 6 年すべて）。
      よってラベル→UTC の多価問題は本コーパスでは発現しない。

書込は必ず ``tmp_path`` の下だけで行う（``data/`` へは 1 バイトも書かない）。
"""
from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import archive_ingest, ingest
from marketdata.mt5_ticks.fakes import CallSpy
from marketdata.mt5_ticks.port import Mt5SupplyError

_HEADER = "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>"

#: 検定で使う銘柄トークン（既存 Dukascopy 木の銘柄と衝突しない別トークン）。
_TOKEN = "JP225@OANDA-Japan-MT5-Live"


def _line(label: str, bid: float, ask: float) -> str:
    """アーカイブ 1 行（``LAST`` / ``VOLUME`` は実ファイルと同じく空）。"""
    return f"{label}\t{bid}\t{ask}\t\t"


def _label_ms(text: str) -> int:
    """``2020.04.30 18:00:00.613`` 形式のラベルを「壁時計 ms」へ（検定側の独立計算）。"""
    stamp = dt.datetime.strptime(text, "%Y.%m.%d %H:%M:%S.%f").replace(
        tzinfo=dt.timezone.utc
    )
    return int(stamp.timestamp() * 1000)


def _write_month(directory, month: str, specs, *, header: bool = True):
    """``ticks_JP225_<month>.zip`` を実ファイルと同じ形式で書く（検定入力）。

    ``specs`` は ``("2020.04.30 18:00:00.613", bid, ask)`` の並び。
    """
    path = directory / f"ticks_JP225_{month}.zip"
    body = [_HEADER] if header else []
    body += [
        _line(label.replace(" ", "\t"), bid, ask) for label, bid, ask in specs
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"ticks_JP225_{month}.csv", "\n".join(body) + "\n")
    return path


def _read_day(day, data_dir):
    """日別 parquet を読む（列は tick 木の権威が決める）。"""
    import pandas as pd

    return pd.read_parquet(
        tick_m1.day_parquet_path(day, symbol=_TOKEN, data_dir=data_dir)
    )


# =====================================================================
# A. parse_lines — TAB テキスト → 行タプル
# =====================================================================

def test_parse_lines_reads_the_label_and_both_quotes_and_skips_the_header():
    """TC-001 正常系: 先頭のヘッダ行を除き ``(ラベル ms, bid, ask)`` を返す。"""
    # Arrange: 実ファイルの先頭 2 行と同一形式。
    lines = [
        _HEADER,
        _line("2020.04.30\t18:00:00.613", 19967.9, 19982.6),
        _line("2020.04.30\t18:00:00.714", 19969.1, 19983.4),
    ]

    # Act
    rows = archive_ingest.parse_lines(lines)

    # Assert: ラベルは**変換しない**（UTC 化は取り込み側の責務）。
    assert rows == [
        (_label_ms("2020.04.30 18:00:00.613"), 19967.9, 19982.6),
        (_label_ms("2020.04.30 18:00:00.714"), 19969.1, 19983.4),
    ]


def test_parse_lines_fails_stop_on_an_unparsable_line_with_its_number():
    """TC-002 異常系: 列数の欠けた行は Fail-Stop（行番号を含む）。"""
    # Arrange: 3 行目だけ列が足りない。
    lines = [
        _HEADER,
        _line("2020.04.30\t18:00:00.613", 19967.9, 19982.6),
        "2020.04.30\t18:00:00.714\t19969.1",
    ]

    # Act / Assert
    with pytest.raises(Mt5SupplyError) as excinfo:
        archive_ingest.parse_lines(lines)
    assert "3" in str(excinfo.value)


# =====================================================================
# B. ingest_months — 月 zip → 日別 parquet（正常系）
# =====================================================================

#: 月末ラベル 17:59/18:00 で切れる実ファイルの並びを最小化した 2 か月分。
#: UTC 日 ``2020-05-31`` が **2 ファイルに跨る**（実測 T8: 53/75 境界で起きる）。
_MAY = [
    ("2020.05.29 10:00:00.000", 20000.0, 20009.0),
    ("2020.05.31 17:59:59.900", 20010.0, 20019.0),
]
_JUNE = [
    ("2020.05.31 18:00:00.100", 20020.0, 20029.0),
    ("2020.06.01 09:00:00.000", 20030.0, 20039.0),
]


def _ingest_may_and_june(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    paths = [_write_month(src, "2020-05", _MAY), _write_month(src, "2020-06", _JUNE)]
    report = archive_ingest.ingest_months(
        paths, symbol_token=_TOKEN, data_dir=tmp_path / "data"
    )
    return report, tmp_path / "data"


def test_ingested_day_has_the_same_columns_and_dtypes_as_the_existing_tree(tmp_path):
    """TC-003 正常系: 列・dtype は既存木（``ingest.rows_to_frame``）と一致する。"""
    # Arrange / Act
    _report, data_dir = _ingest_may_and_june(tmp_path)

    # Assert: 権威が作る姿と 1 バイトも違わない。
    frame = _read_day(dt.date(2020, 5, 29), data_dir)
    expected = ingest.rows_to_frame([(_label_ms("2020.05.29 10:00:00.000"), 20000.0, 20009.0)])
    assert list(frame.columns) == list(expected.columns)
    assert [str(d) for d in frame.dtypes] == [str(d) for d in expected.dtypes]
    assert frame.reset_index(drop=True).equals(expected.reset_index(drop=True))


def test_a_utc_day_split_across_two_month_files_lands_in_one_parquet(tmp_path):
    """TC-004 境界: 月跨ぎの UTC 日は 1 つの parquet に統合され、行数は両月の合算。"""
    # Arrange / Act
    _report, data_dir = _ingest_may_and_june(tmp_path)

    # Assert
    frame = _read_day(dt.date(2020, 5, 31), data_dir)
    assert len(frame) == 2
    assert list(frame["bidPrice"]) == [20010.0, 20020.0]


def test_summer_labels_convert_with_plus_three_hours(tmp_path):
    """TC-005 正常系: 夏（EEST）は UTC+3（T1b 固定点 2021-04 / 2026-08 と同じ規則）。"""
    # Arrange / Act
    _report, data_dir = _ingest_may_and_june(tmp_path)

    # Assert: ラベル 18:00:00.100 → UTC 15:00:00.100。
    frame = _read_day(dt.date(2020, 5, 31), data_dir)
    assert str(frame["timestamp"].iloc[1]) == "2020-05-31 15:00:00.100000+00:00"


def test_winter_labels_convert_with_plus_two_hours(tmp_path):
    """TC-006 正常系: 冬（EET）は UTC+2（T1b 固定点 2021-01 の先頭ラベル ``12.31 17:00``）。"""
    # Arrange
    src = tmp_path / "src"
    src.mkdir()
    path = _write_month(src, "2021-01", [("2020.12.31 17:00:00.000", 27000.0, 27010.0)])

    # Act
    archive_ingest.ingest_months(
        [path], symbol_token=_TOKEN, data_dir=tmp_path / "data"
    )

    # Assert: ラベル 17:00 → UTC 15:00（+2）。日 partition も UTC 日で切れている。
    frame = _read_day(dt.date(2020, 12, 31), tmp_path / "data")
    assert str(frame["timestamp"].iloc[0]) == "2020-12-31 15:00:00+00:00"


def test_report_accounts_for_every_row_that_was_read(tmp_path):
    """TC-007 正常系: report は読んだ行の行方（書いた / スキップ）を全部持つ。"""
    # Arrange / Act
    report, _data_dir = _ingest_may_and_june(tmp_path)

    # Assert
    assert report.months == ("2020-05", "2020-06")
    assert report.rows_read == 4
    assert report.rows_written == 4
    assert report.rows_skipped_existing == 0
    assert report.days_written == 3


# =====================================================================
# C. 境界 — 休場日の ``.empty`` と既存日のスキップ
# =====================================================================

def test_a_calendar_day_without_rows_inside_the_range_gets_an_empty_marker(tmp_path):
    """TC-008 境界: 範囲内の 0 行日は ``.empty``。範囲の外には何も置かない。"""
    # Arrange / Act: 5/29・5/31・6/1 に行があり、5/30（休場）だけ 0 行。
    report, data_dir = _ingest_may_and_june(tmp_path)

    # Assert
    def marker(day):
        return tick_m1.day_empty_marker_path(day, symbol=_TOKEN, data_dir=data_dir)

    assert marker(dt.date(2020, 5, 30)).is_file()
    assert not marker(dt.date(2020, 5, 28)).exists()
    assert not marker(dt.date(2020, 6, 2)).exists()
    assert report.days_empty == 1


def test_a_day_that_already_has_a_parquet_is_skipped_byte_for_byte(tmp_path):
    """TC-009 境界: 既存 parquet の日は**上書きしない**（内容・mtime とも不変）。"""
    # Arrange: 1 回目の取り込みで木を作る。
    report_first, data_dir = _ingest_may_and_june(tmp_path)
    parquet = tick_m1.day_parquet_path(dt.date(2020, 5, 29), symbol=_TOKEN, data_dir=data_dir)
    before = (parquet.read_bytes(), parquet.stat().st_mtime_ns)

    # Act: 同じ月をもう一度取り込む。
    src = tmp_path / "src"
    report = archive_ingest.ingest_months(
        [src / "ticks_JP225_2020-05.zip", src / "ticks_JP225_2020-06.zip"],
        symbol_token=_TOKEN,
        data_dir=data_dir,
    )

    # Assert: 1 バイトも書き換わっていない。読んだ行はスキップとして計上される。
    assert (parquet.read_bytes(), parquet.stat().st_mtime_ns) == before
    assert report_first.days_written == 3
    assert report.days_written == 0
    assert report.days_skipped_existing == 3
    assert report.rows_written == 0
    assert report.rows_skipped_existing == 4
    assert report.rows_unaccounted == 0


# =====================================================================
# D. 異常系 — Fail-Stop（部分的に書かれた台帳を残さない）
# =====================================================================

def _ingest_single_month(tmp_path, month, specs, *, extra_line=None):
    src = tmp_path / "src"
    src.mkdir()
    path = _write_month(src, month, specs)
    if extra_line is not None:
        # 壊れた行を末尾に足して zip を作り直す（実ファイル形式のまま 1 行だけ壊す）。
        with zipfile.ZipFile(path) as archive:
            name = archive.namelist()[0]
            text = archive.read(name).decode("ascii")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, text + extra_line + "\n")
    return archive_ingest.ingest_months(
        [path], symbol_token=_TOKEN, data_dir=tmp_path / "data"
    )


def _written_files(tmp_path):
    data_dir = tmp_path / "data"
    return sorted(p.name for p in data_dir.rglob("*") if p.is_file())


def test_an_unparsable_line_stops_the_ingest_without_writing_anything(tmp_path):
    """TC-010 異常系: 解釈できない行があれば Fail-Stop（書込 0）。"""
    # Arrange / Act / Assert
    with pytest.raises(Mt5SupplyError):
        _ingest_single_month(
            tmp_path,
            "2020-05",
            [("2020.05.29 10:00:00.000", 20000.0, 20009.0)],
            extra_line="2020.05.29\t10:00:01.000\tNOT_A_PRICE\t20009.0\t\t",
        )
    assert _written_files(tmp_path) == []


def test_a_backwards_utc_timestamp_stops_the_ingest_without_writing_anything(tmp_path):
    """TC-011 異常系: UTC が戻る並びは Fail-Stop（書込 0）。"""
    # Arrange: 同一日で時刻が戻る（実測 T8 では起きない＝起きたら供給側の破損）。
    specs = [
        ("2020.05.29 10:00:00.000", 20000.0, 20009.0),
        ("2020.05.29 09:59:59.000", 20001.0, 20010.0),
    ]

    # Act / Assert
    with pytest.raises(Mt5SupplyError):
        _ingest_single_month(tmp_path, "2020-05", specs)
    assert _written_files(tmp_path) == []


def test_a_non_positive_bid_stops_the_ingest_without_writing_anything(tmp_path):
    """TC-012 異常系: ``bid <= 0`` は Fail-Stop（気配の実在性・``ingest.validate_rows``）。"""
    # Arrange
    specs = [
        ("2020.05.29 10:00:00.000", 0.0, 20009.0),
        ("2020.05.29 10:00:01.000", 20001.0, 20010.0),
    ]

    # Act / Assert
    with pytest.raises(Mt5SupplyError):
        _ingest_single_month(tmp_path, "2020-05", specs)
    assert _written_files(tmp_path) == []


def test_a_file_name_without_a_month_stops_the_ingest(tmp_path):
    """TC-013 異常系: ``*_YYYY-MM.zip`` でない入力は Fail-Stop（読む順序を決められない）。"""
    # Arrange
    src = tmp_path / "src"
    src.mkdir()
    path = src / "ticks_JP225.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("x.csv", _HEADER + "\n")

    # Act / Assert
    with pytest.raises(Mt5SupplyError):
        archive_ingest.ingest_months(
            [path], symbol_token=_TOKEN, data_dir=tmp_path / "data"
        )


# =====================================================================
# E. 計算量（絶対命令・Test Spy）— 発行した計算 − 出力に使った計算 = 0
#
# 状態検証（parquet の中身）では「作ってから捨てる」欠陥は**原理的に落ちない**
# （ISSUE-450）。ここで固定するのは回数そのものではなく **無駄の不在**である。
# =====================================================================

def _months_for(tmp_path, count):
    """``count`` か月ぶんの入力（各月 2 日・1 日 2 行）。"""
    src = tmp_path / "src"
    src.mkdir()
    paths = []
    for index in range(count):
        month = 5 + index
        paths.append(_write_month(src, f"2020-{month:02d}", [
            (f"2020.{month:02d}.10 10:00:00.000", 20000.0, 20009.0),
            (f"2020.{month:02d}.10 10:00:01.000", 20001.0, 20010.0),
            (f"2020.{month:02d}.11 10:00:00.000", 20002.0, 20011.0),
        ]))
    return paths


@pytest.mark.parametrize("months", [1, 2])
def test_every_row_that_is_read_is_either_written_or_reported_as_skipped(tmp_path, monkeypatch, months):
    """CX-a: 読んだ行 − 書いた行 − スキップ日の行 = 0。フレーム化も書いた行だけ。"""
    # Arrange: 行 → DataFrame の発行を数える（measure は「フレーム化した行数」）。
    spy = CallSpy(target=ingest.rows_to_frame, measure=lambda rows: len(rows))
    monkeypatch.setattr(ingest, "rows_to_frame", spy)
    paths = _months_for(tmp_path, months)

    # Act
    report = archive_ingest.ingest_months(
        paths, symbol_token=_TOKEN, data_dir=tmp_path / "data"
    )

    # Assert: 捨てた行は無く、作ったフレームは全部書かれている。
    assert report.rows_unaccounted == 0
    assert spy.total == report.rows_written


@pytest.mark.parametrize("months", [1, 2])
def test_each_month_archive_is_opened_exactly_once(tmp_path, monkeypatch, months):
    """CX-b: zip を開く回数は月数ちょうど（開き直し＝読み直しを作らない）。"""
    # Arrange
    spy = CallSpy(target=archive_ingest.open_month_zip)
    monkeypatch.setattr(archive_ingest, "open_month_zip", spy)
    paths = _months_for(tmp_path, months)

    # Act
    archive_ingest.ingest_months(paths, symbol_token=_TOKEN, data_dir=tmp_path / "data")

    # Assert: 発行は「入力の月数」だけで決まる（2 点で固定＝オーダーの表明）。
    opened = [str(args[0]) for args, _kwargs in spy.calls]
    assert sorted(opened) == sorted(str(p) for p in paths)
    assert spy.count == months


def test_dry_run_writes_nothing_and_builds_no_frames(tmp_path, monkeypatch):
    """CX-c: ``--dry-run`` は 1 バイトも書かず、捨てるためのフレームも作らない。"""
    # Arrange
    spy = CallSpy(target=ingest.rows_to_frame, measure=lambda rows: len(rows))
    monkeypatch.setattr(ingest, "rows_to_frame", spy)
    paths = _months_for(tmp_path, 2)

    # Act
    report = archive_ingest.ingest_months(
        paths,
        symbol_token=_TOKEN,
        data_dir=tmp_path / "data",
        writer=archive_ingest.DryRunWriter(),
    )

    # Assert: 判断（何日・何行を書くか）は返るが、実体は 1 つも作られない。
    assert report.rows_read == 6
    assert report.days_written == 4
    assert spy.count == 0
    assert not (tmp_path / "data").exists()


# =====================================================================
# F. 非干渉 — 既存 Dukascopy 木に 1 バイトも触らない（段階 2 の通過条件）
# =====================================================================

def test_the_dukascopy_listing_is_identical_before_and_after_the_ingest(tmp_path):
    """TC-014 非干渉: ``day_parquet_files(symbol="JP225")`` が取り込み前後で完全一致。

    ISSUE-447 段階 2 の通過条件そのもの。別トークンで同居する設計が守られていれば、
    既存木の列挙は 1 件も変わらない。
    """
    # Arrange: 同じ tick 木の下に Dukascopy 風（symbol="JP225"）の日別ファイルを置く。
    #   取り込む日（5/29・5/31・6/1）とは**別の日**に置く。同じ日に置くと、既存日スキップ
    #   によって「トークンを無視して JP225 として書く」欠陥が隠れる（実際に mutant が
    #   生き残ったため、この配置が検定の成立条件である）。
    data_dir = tmp_path / "data"
    for day in (dt.date(2020, 5, 28), dt.date(2020, 6, 2)):
        path = tick_m1.day_parquet_path(day, symbol="JP225", data_dir=data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"dukascopy")
    listing = tick_m1.day_parquet_files(
        dt.date(2020, 5, 1), dt.date(2020, 6, 30), symbol="JP225", data_dir=data_dir
    )
    before = [(p, p.read_bytes(), p.stat().st_mtime_ns) for p in listing]

    # Act
    src = tmp_path / "src"
    src.mkdir()
    archive_ingest.ingest_months(
        [_write_month(src, "2020-05", _MAY), _write_month(src, "2020-06", _JUNE)],
        symbol_token=_TOKEN,
        data_dir=data_dir,
    )

    # Assert: 列挙も中身も mtime も動いていない。
    after_listing = tick_m1.day_parquet_files(
        dt.date(2020, 5, 1), dt.date(2020, 6, 30), symbol="JP225", data_dir=data_dir
    )
    assert after_listing == listing
    assert [(p, p.read_bytes(), p.stat().st_mtime_ns) for p in after_listing] == before
    # 陽性対照: 取り込みは確かに起きている（別トークン側に 3 日ぶん在る）。
    assert len(tick_m1.day_parquet_files(
        dt.date(2020, 5, 1), dt.date(2020, 6, 30), symbol=_TOKEN, data_dir=data_dir
    )) == 3


# =====================================================================
# G. 実アーカイブのスモーク（1 件・読み取りのみ・書込は tmp_path）
# =====================================================================

_REAL_ZIP = Path(__file__).resolve().parents[2] / "data" / "test" / "ticks_JP225_2020-05.zip"


@pytest.mark.skipif(not _REAL_ZIP.is_file(), reason=f"実アーカイブが無い: {_REAL_ZIP}")
def test_the_real_may_2020_archive_ingests_to_the_measured_row_count(tmp_path):
    """TC-015 スモーク: 実ファイル 1 件を tmp_path へ取り込み、ISSUE-447 の実測値と一致する。

    ISSUE-447 T1b の「225,381 行 / 22 日」のうち **22 日はラベル日**（``<DATE>`` 列）であり、
    UTC 日 partition の数ではない。ラベル 00:00〜02:59 は UTC では前日に落ちるため、
    UTC 日は 26（+ 範囲内の 0 行日 4）になる。両方を固定して取り違えを防ぐ。
    """
    # Arrange
    data_dir = tmp_path / "data"
    with zipfile.ZipFile(_REAL_ZIP) as archive:
        text = archive.read(archive.namelist()[0]).decode("ascii")
    label_days = {line.split("\t", 1)[0] for line in text.splitlines()[1:] if line}

    # Act
    report = archive_ingest.ingest_months(
        [_REAL_ZIP], symbol_token=_TOKEN, data_dir=data_dir
    )

    # Assert: 行数・日数
    assert report.rows_read == 225381          # ISSUE-447 実測値
    assert len(label_days) == 22               # ISSUE-447 実測値（ラベル日）
    assert report.rows_written == 225381
    assert report.rows_unaccounted == 0
    assert report.days_written == 26           # UTC 日 partition
    assert report.days_empty == 4

    # Assert: 木に入った行の総数と、全行が 0.1 格子に載ること（T1b: 225,381/225,381）。
    import pandas as pd

    files = tick_m1.day_parquet_files(
        dt.date(2020, 4, 1), dt.date(2020, 6, 30), symbol=_TOKEN, data_dir=data_dir
    )
    assert len(files) == 26
    frame = pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)
    assert len(frame) == 225381
    for column in ("bidPrice", "askPrice"):
        scaled = frame[column] * 10.0
        assert ((scaled - scaled.round()).abs() < 1e-6).all()


def test_a_non_ascii_byte_is_reported_as_a_supply_error(tmp_path):
    """TC-021 異常系: ASCII でないバイトも Fail-Stop の型（``Mt5SupplyError``）で止まる。

    実測では全 76 か月が ASCII だが、型が揃っていないと「壊れた入力」が
    別種の例外として上位へ抜け、Fail-Stop の扱い（書込 0・報告）から外れる。
    """
    # Arrange: 中身のバイト列を直接壊す（テキストでは表せない入力）。
    src = tmp_path / "src"
    src.mkdir()
    path = src / "ticks_JP225_2020-05.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ticks_JP225_2020-05.csv",
            _HEADER.encode("ascii") + b"\n2020.05.29\t10:00:00.000\t2\xff0.0\t20009.0\t\t\n",
        )

    # Act / Assert
    with pytest.raises(Mt5SupplyError):
        archive_ingest.ingest_months(
            [path], symbol_token=_TOKEN, data_dir=tmp_path / "data"
        )
    assert _written_files(tmp_path) == []
