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

import ast
import datetime as dt
import zipfile
from pathlib import Path

import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import archive_ingest, ingest, journal
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
#:
#: 両端に「捨て日」を 1 日ずつ置いてあるのは、検査したい日（5/29・5/31・6/1）を走査範囲の
#: **内側**へ入れるためである。範囲の端の日は不完全でありうるので書かれない（TC-022/023）。
#: 端に置いたままだと、検定が「書かれない日」を読もうとして題意を失う。
_MAY = [
    ("2020.05.28 10:00:00.000", 19990.0, 19999.0),   # 走査の先頭 UTC 日＝切り落とし
    ("2020.05.29 10:00:00.000", 20000.0, 20009.0),
    ("2020.05.31 17:59:59.900", 20010.0, 20019.0),
]
_JUNE = [
    ("2020.05.31 18:00:00.100", 20020.0, 20029.0),
    ("2020.06.01 09:00:00.000", 20030.0, 20039.0),
    ("2020.06.02 09:00:00.000", 20040.0, 20049.0),   # 走査の末尾 UTC 日＝持ち越し
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
    path = _write_month(src, "2021-01", [
        ("2020.12.30 17:00:00.000", 26900.0, 26910.0),   # 先頭＝切り落とし
        ("2020.12.31 17:00:00.000", 27000.0, 27010.0),   # 内側＝書かれる日
        ("2021.01.04 17:00:00.000", 27100.0, 27110.0),   # 末尾＝持ち越し
    ])

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

    # Assert: 書いた 4 行 + 切り落とし 1 行 + 持ち越し 1 行 = 読んだ 6 行。
    assert report.months == ("2020-05", "2020-06")
    assert report.rows_read == 6
    assert report.rows_written == 4
    assert report.rows_skipped_existing == 0
    assert report.days_written == 3
    assert report.rows_head_dropped == 1
    assert report.rows_carried == 1
    assert report.rows_unaccounted == 0


# =====================================================================
# B-2. 範囲端 — 不完全な UTC 日を「完成品」として書かない（工程 5 🔴-1）
#
# 走査範囲の端にある UTC 日は、**その日の全行を見たとは言えない**。
#   - 末尾: 次の UTC 日の行が来ていない＝まだ増えるかもしれない。
#   - 先頭: 先頭 zip は前月末日の途中から始まる（実測: 2020-05 の先頭ラベルは
#     ``2020.04.30``）ため、その日の頭が欠けている可能性を排除できない。
# 端の日を書いてしまうと、既存日スキップがその欠けを**恒久化**する（実測: 月分割実行で
# 2020-06-30 が 4,173 行のまま凍結・一括なら 5,673 行）。
# =====================================================================

#: 1 か月 3 UTC 日（先頭 / 内側 / 末尾）。端の扱いだけを見るための最小形。
_THREE_DAYS = [
    ("2020.05.11 10:00:00.000", 20000.0, 20009.0),  # 走査の先頭 UTC 日
    ("2020.05.12 10:00:00.000", 20010.0, 20019.0),  # 内側（唯一「閉じた」日）
    ("2020.05.13 10:00:00.000", 20020.0, 20029.0),  # 末尾＝開いたまま
]


def _ingest_three_days(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    path = _write_month(src, "2020-05", _THREE_DAYS)
    report = archive_ingest.ingest_months(
        [path], symbol_token=_TOKEN, data_dir=tmp_path / "data"
    )
    return report, tmp_path / "data"


def test_the_open_day_at_the_end_of_the_scan_is_carried_not_written(tmp_path):
    """TC-022 境界: 走査終了時に開いている UTC 日は**書かず**持ち越しとして報告する。"""
    # Arrange / Act
    report, data_dir = _ingest_three_days(tmp_path)

    # Assert: 末尾日は 1 バイトも書かれていない（欠けたまま凍結させない）。
    assert not tick_m1.day_parquet_path(
        dt.date(2020, 5, 13), symbol=_TOKEN, data_dir=data_dir
    ).is_file()
    assert report.days_carried == 1
    assert report.rows_carried == 1


def test_the_first_utc_day_of_the_scan_is_dropped_not_written(tmp_path):
    """TC-023 境界: 走査全体の最初の UTC 日は**書かず**切り落としとして報告する。"""
    # Arrange / Act
    report, data_dir = _ingest_three_days(tmp_path)

    # Assert: 先頭日は書かれず、内側の日だけが書かれる。
    assert not tick_m1.day_parquet_path(
        dt.date(2020, 5, 11), symbol=_TOKEN, data_dir=data_dir
    ).is_file()
    assert tick_m1.day_parquet_path(
        dt.date(2020, 5, 12), symbol=_TOKEN, data_dir=data_dir
    ).is_file()
    assert report.head_dropped_day == dt.date(2020, 5, 11)
    assert report.rows_head_dropped == 1
    assert report.days_written == 1
    # 読んだ行の行方は全部説明がつく（切り落とし・持ち越しも「行方」である）。
    assert report.rows_unaccounted == 0


def _written_days(data_dir):
    """``data_dir`` の木に在る日別 parquet を ``{日: バイト列}`` で返す。"""
    return {
        p.parts[-4:-1]: p.read_bytes()
        for p in tick_m1.day_parquet_files(
            dt.date(2020, 4, 1), dt.date(2020, 7, 1), symbol=_TOKEN, data_dir=data_dir
        )
    }


def test_splitting_the_run_by_month_never_freezes_a_partial_day(tmp_path):
    """TC-024 同値: 月分割でも一括でも、書かれた日は**バイト等価**である。

    是正前の欠陥（工程 5 🔴-1 の実測）:
        月分割実行では月跨ぎの UTC 日が「その月にある行だけ」で書かれ、次の月の実行は
        既存日スキップでそれを恒久化していた（2020-06-30 が 4,173 行で凍結・一括なら
        5,673 行）。**中身の違う同名の日**が出来るのが欠陥の本体である。

    集合として一致しない理由（レビュー指摘「同一の日集合」からの是正・2026-09-02 実測）:
        月跨ぎの日は、前月の実行では「開いた日」・翌月の実行では「先頭日」であり、
        どちらの実行でも完全な姿を持たない。「不完全な日を書かない」という規則の下では
        **分割実行がその日を書けないことが正しい**。よって集合は一致せず、差は
        「内部の月境界日」ちょうどになる。集合一致を要求すると、不完全な日を書けという
        要求と同じになる（是正の目的そのものを打ち消す）。
    """
    # Arrange
    src = tmp_path / "src"
    src.mkdir()
    may = _write_month(src, "2020-05", _MAY)
    june = _write_month(src, "2020-06", _JUNE)
    boundary = ("2020", "05", "31")

    # Act: 一括 1 回 / 月ごとに 2 回。
    bulk_dir = tmp_path / "bulk"
    archive_ingest.ingest_months([may, june], symbol_token=_TOKEN, data_dir=bulk_dir)
    split_dir = tmp_path / "split"
    first = archive_ingest.ingest_months([may], symbol_token=_TOKEN, data_dir=split_dir)
    second = archive_ingest.ingest_months([june], symbol_token=_TOKEN, data_dir=split_dir)

    bulk, split = _written_days(bulk_dir), _written_days(split_dir)

    # Assert 1: 両方に在る日は 1 バイトも違わない（部分的な日を凍結していない）。
    assert {d: split[d] for d in split.keys() & bulk.keys()} == {
        d: bulk[d] for d in split.keys() & bulk.keys()
    }
    # Assert 2: 分割が余計な日を作っていない。
    assert split.keys() - bulk.keys() == set()
    # Assert 3: 足りない日は「内部の月境界日」ちょうど（他の日は 1 つも落ちない）。
    assert bulk.keys() - split.keys() == {boundary}
    # Assert 4: その日は黙って消えたのではなく、両方の実行の報告に出ている。
    #   持ち越した行 + 切り落とした行 = 一括で書かれたその日の行数（1 行も失われていない）。
    assert first.days_carried == 1
    assert second.head_dropped_day == dt.date(2020, 5, 31)
    assert first.rows_carried + second.rows_head_dropped == len(
        _read_day(dt.date(2020, 5, 31), bulk_dir)
    )
    assert first.rows_unaccounted == 0 and second.rows_unaccounted == 0


# =====================================================================
# C. 境界 — 休場日の ``.empty`` と既存日のスキップ
# =====================================================================

def test_a_calendar_day_without_rows_inside_the_range_gets_an_empty_marker(tmp_path):
    """TC-008 境界: **書いた**範囲内の 0 行日は ``.empty``。範囲の外には何も置かない。

    範囲は「書いた日の最初〜最後」である。切り落とした先頭日・持ち越した末尾日を範囲に
    含めると、書いていない日の周りに ``.empty`` を置くことになる（「取れていない」と
    「行が無い」の区別が消える）。
    """
    # Arrange / Act: 5/29・5/31・6/1 を書き、5/30（休場）だけ 0 行。
    report, data_dir = _ingest_may_and_june(tmp_path)

    # Assert
    def marker(day):
        return tick_m1.day_empty_marker_path(day, symbol=_TOKEN, data_dir=data_dir)

    assert marker(dt.date(2020, 5, 30)).is_file()
    # 5/28 は切り落とした先頭日、6/2 は持ち越した末尾日。どちらも書いた範囲の外。
    assert not marker(dt.date(2020, 5, 28)).exists()
    assert not marker(dt.date(2020, 6, 2)).exists()
    assert report.days_empty == 1


def test_days_between_the_dropped_head_and_the_first_written_day_are_marked_empty(tmp_path):
    """TC-037 境界: 切り落とし日〜持ち越し日の**内側**は、書かれない日も含めて観測済み。

    実コーパスで起きる形: 月 zip の先頭ラベルは前月末日（例 ``2020.07.31`` 金）であり、
    月初が土日なら 8/1・8/2 は「走査したが行が 0」である。ここに ``.empty`` を置かないと、
    その日は「取れていない」と見分けがつかなくなる。

    範囲を「書いた日の最初〜最後」に狭めると、この 2 日が範囲から落ちる。正しい範囲は
    **切り落とした先頭日と持ち越した末尾日の間（両端は除く）**である。両端は観測が
    完結していないので、その 2 日には何も置かない。
    """
    # Arrange: 5/8（先頭）・5/11・5/12（書く）・5/13（持ち越し）。5/9・5/10 は 0 行。
    src = tmp_path / "src"
    src.mkdir()
    path = _write_month(src, "2020-05", [
        ("2020.05.08 10:00:00.000", 20000.0, 20009.0),
        ("2020.05.11 10:00:00.000", 20010.0, 20019.0),
        ("2020.05.12 10:00:00.000", 20020.0, 20029.0),
        ("2020.05.13 10:00:00.000", 20030.0, 20039.0),
    ])

    # Act
    report = archive_ingest.ingest_months(
        [path], symbol_token=_TOKEN, data_dir=tmp_path / "data"
    )

    # Assert
    def marker(day):
        return tick_m1.day_empty_marker_path(day, symbol=_TOKEN, data_dir=tmp_path / "data")

    assert marker(dt.date(2020, 5, 9)).is_file()
    assert marker(dt.date(2020, 5, 10)).is_file()
    assert report.days_empty == 2
    # 端は観測が完結していない。何も置かない（「行が無い」と言い切らない）。
    assert not marker(dt.date(2020, 5, 8)).exists()
    assert not marker(dt.date(2020, 5, 13)).exists()


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
# C-2. 既存日スキップは**中身に依存する**（工程 5 🔴-2）
#
# 「在るからスキップ」は、既存が何であってもスキップする。壊れた日・欠けた日が在っても
# 黙って通り、以後の実行がそれを恒久化する。スキップしてよいのは**同じものが在るとき**
# だけであり、違うものが在るのは供給か台帳の異常なので Fail-Stop にする。
# =====================================================================

def _existing_day_path(data_dir):
    return tick_m1.day_parquet_path(dt.date(2020, 5, 29), symbol=_TOKEN, data_dir=data_dir)


def _reingest_may_and_june(tmp_path, data_dir):
    src = tmp_path / "src"
    return archive_ingest.ingest_months(
        [src / "ticks_JP225_2020-05.zip", src / "ticks_JP225_2020-06.zip"],
        symbol_token=_TOKEN,
        data_dir=data_dir,
    )


def _overwrite_day_with(rows, data_dir):
    """既存日を ``rows`` の内容で置き換える（検定用の「食い違う既存」を作る）。"""
    journal.write_parquet_atomically(ingest.rows_to_frame(rows), _existing_day_path(data_dir))


def test_an_existing_day_with_the_same_content_is_skipped(tmp_path):
    """TC-025 正常系: 既存日の中身が入力と一致していればスキップする（従来どおり）。"""
    # Arrange
    _first, data_dir = _ingest_may_and_june(tmp_path)

    # Act
    report = _reingest_may_and_june(tmp_path, data_dir)

    # Assert
    assert report.days_skipped_existing == 3
    assert report.rows_written == 0
    assert report.rows_unaccounted == 0


def test_an_existing_day_with_a_different_row_count_stops_the_ingest(tmp_path):
    """TC-026 異常系: 既存日の**行数**が入力と違えば Fail-Stop（日と両方の行数を報せる）。"""
    # Arrange: 5/29 の parquet を 2 行に差し替える（入力は 1 行）。
    _first, data_dir = _ingest_may_and_june(tmp_path)
    label = _label_ms("2020.05.29 10:00:00.000")
    _overwrite_day_with([(label, 20000.0, 20009.0), (label + 1000, 20001.0, 20010.0)], data_dir)

    # Act / Assert
    with pytest.raises(Mt5SupplyError) as excinfo:
        _reingest_may_and_june(tmp_path, data_dir)
    message = str(excinfo.value)
    assert "2020-05-29" in message
    assert "2" in message and "1" in message


def test_an_existing_day_with_different_endpoints_stops_the_ingest(tmp_path):
    """TC-027 異常系: 行数が同じでも**先頭 / 末尾 UTC ms** が違えば Fail-Stop。

    行数だけの照合では、同じ本数で中身が入れ替わった日（別の時間帯の行）を通してしまう。
    """
    # Arrange: 行数は 1 のまま、時刻だけずらす。
    _first, data_dir = _ingest_may_and_june(tmp_path)
    _overwrite_day_with([(_label_ms("2020.05.29 11:00:00.000"), 20000.0, 20009.0)], data_dir)

    # Act / Assert
    with pytest.raises(Mt5SupplyError) as excinfo:
        _reingest_may_and_june(tmp_path, data_dir)
    assert "2020-05-29" in str(excinfo.value)


def test_an_unreadable_existing_day_is_reported_as_a_supply_error(tmp_path):
    """TC-038 異常系: 既存日が parquet として読めない場合も Fail-Stop の型で止まる。

    型が揃っていないと、壊れた台帳が別種の例外として上位へ抜け、Fail-Stop の扱い
    （書込 0・報告）から外れる（TC-021 と同じ理由）。
    """
    # Arrange: 既存日を parquet でないバイト列に置き換える。
    _first, data_dir = _ingest_may_and_june(tmp_path)
    _existing_day_path(data_dir).write_bytes(b"not a parquet")

    # Act / Assert
    with pytest.raises(Mt5SupplyError):
        _reingest_may_and_june(tmp_path, data_dir)


def test_dry_run_also_stops_on_a_mismatching_existing_day(tmp_path):
    """TC-039 境界: ``--dry-run`` も食い違う既存日で止まる（事前確認として使えること）。

    dry-run が「書かないから照合もしない」だと、本番実行で初めて中止することになる。
    1 バイトも書かずに台帳の食い違いを見つけられることが、事前確認の値打ちである。
    """
    # Arrange
    _first, data_dir = _ingest_may_and_june(tmp_path)
    _overwrite_day_with([(_label_ms("2020.05.29 11:00:00.000"), 20000.0, 20009.0)], data_dir)
    src = tmp_path / "src"

    # Act / Assert
    with pytest.raises(Mt5SupplyError):
        archive_ingest.ingest_months(
            [src / "ticks_JP225_2020-05.zip", src / "ticks_JP225_2020-06.zip"],
            symbol_token=_TOKEN,
            data_dir=data_dir,
            writer=archive_ingest.DryRunWriter(),
        )


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


def test_zips_of_different_pairs_stop_the_ingest(tmp_path):
    """TC-028 異常系: 銘柄の違う zip が混ざっていれば Fail-Stop（1 つの木へ混ぜない）。"""
    # Arrange: ファイル名の銘柄部が JP225 と USDJPY で食い違う。
    src = tmp_path / "src"
    src.mkdir()
    jp = _write_month(src, "2020-05", _MAY)
    other = src / "ticks_USDJPY_2020-06.zip"
    with zipfile.ZipFile(other, "w") as archive:
        archive.writestr("ticks_USDJPY_2020-06.csv", _HEADER + "\n")

    # Act / Assert
    with pytest.raises(Mt5SupplyError) as excinfo:
        archive_ingest.ingest_months(
            [jp, other], symbol_token=_TOKEN, data_dir=tmp_path / "data"
        )
    assert "USDJPY" in str(excinfo.value)
    assert _written_files(tmp_path) == []


def test_a_zip_whose_pair_differs_from_the_token_stops_the_ingest(tmp_path):
    """TC-029 異常系: zip の銘柄とトークンの銘柄部が違えば Fail-Stop。

    ここが無いと、JP225 のアーカイブを USDJPY のトークンで指定して**別銘柄の木へ**
    書き込める（工程 5 🔴-3: Dukascopy 木へ構造的に到達しうる経路）。
    """
    # Arrange
    src = tmp_path / "src"
    src.mkdir()
    jp = _write_month(src, "2020-05", _MAY)
    wrong_token = "USDJPY" + ingest.TOKEN_SEPARATOR + "OANDA-Japan-MT5-Live"

    # Act / Assert
    with pytest.raises(Mt5SupplyError) as excinfo:
        archive_ingest.ingest_months(
            [jp], symbol_token=wrong_token, data_dir=tmp_path / "data"
        )
    message = str(excinfo.value)
    assert "JP225" in message and "USDJPY" in message
    assert _written_files(tmp_path) == []


def test_the_pair_is_read_from_the_archive_file_name(tmp_path):
    """TC-030 正常系: ``ticks_<PAIR>_YYYY-MM.zip`` から銘柄を読む（読む場所は 1 つ）。"""
    # Arrange
    src = tmp_path / "src"
    src.mkdir()
    path = _write_month(src, "2020-05", _MAY)

    # Act / Assert
    assert archive_ingest.pair_of(path) == "JP225"


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

#: 1 か月ぶんの行数（``_months_for`` の 1 か月＝ 4 UTC 日・5 行）。
_ROWS_PER_MONTH = 5


def _months_for(tmp_path, count):
    """``count`` か月ぶんの入力（各月 4 日・計 5 行）。

    4 日にしてあるのは、範囲の端（先頭 1 日・末尾 1 日）が書かれないためである。
    2 日だと 1 か月の実行で書かれる日が 0 になり、``spy.total == rows_written`` が
    ``0 == 0`` の空虚な等式になる（無駄の不在を何も表明しなくなる）。
    """
    src = tmp_path / "src"
    src.mkdir()
    paths = []
    for index in range(count):
        month = 5 + index
        paths.append(_write_month(src, f"2020-{month:02d}", [
            (f"2020.{month:02d}.09 10:00:00.000", 19999.0, 20008.0),
            (f"2020.{month:02d}.10 10:00:00.000", 20000.0, 20009.0),
            (f"2020.{month:02d}.10 10:00:01.000", 20001.0, 20010.0),
            (f"2020.{month:02d}.11 10:00:00.000", 20002.0, 20011.0),
            (f"2020.{month:02d}.12 10:00:00.000", 20003.0, 20012.0),
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


class _RecordingWriter:
    """書かずに「いつ・何行で書こうとしたか」だけを記録する writer。"""

    def __init__(self, counter):
        self._counter = counter
        #: ``(その時点で読んだ行数, 書こうとした行数)`` の並び。
        self.calls = []

    def write_day(self, rows, path):
        self.calls.append((self._counter["lines"], len(rows)))

    def write_marker(self, path):
        return None


def _one_month_of(tmp_path, days, rows_per_day):
    """1 か月 = ``days`` UTC 日 × ``rows_per_day`` 行の zip を 1 本作る。"""
    src = tmp_path / "src"
    src.mkdir()
    specs = [
        (f"2020.05.{day + 5:02d} 10:00:{row:02d}.000", 20000.0 + row, 20009.0 + row)
        for day in range(days)
        for row in range(rows_per_day)
    ]
    return _write_month(src, "2020-05", specs), len(specs) + 1  # +1 はヘッダ行


def _count_lines_through(monkeypatch, counter):
    """``open_month_zip`` を「流れた行を数える」ラッパへ差し替える。"""
    import contextlib as _contextlib

    real = archive_ingest.open_month_zip

    @_contextlib.contextmanager
    def counting(zip_path):
        with real(zip_path) as lines:
            def counted():
                for line in lines:
                    counter["lines"] += 1
                    yield line
            yield counted()

    monkeypatch.setattr(archive_ingest, "open_month_zip", counting)


@pytest.mark.parametrize("days", [4, 8])
def test_the_ingest_writes_days_while_still_reading_the_archive(tmp_path, monkeypatch, days):
    """CX-e: 1 回の ``write_day`` が扱う行数は**入力の総行数に比例しない**（最大日行数で頭打ち）。

    月 1 本は最大 474 万行ある。月を丸ごとリストへ載せる実装（``parse_lines``）でも
    parquet の中身は同じになるため、**状態検証では原理的に落ちない**（ISSUE-450 と同型）。
    ここで固定するのは回数ではなく「入力 total に比例して溜め込まない」という無駄の不在で、
    行数 N / 2N の 2 点で同じ値になることをもってオーダーを表明する。
    """
    # Arrange
    rows_per_day = 3
    counter = {"lines": 0}
    path, total_lines = _one_month_of(tmp_path, days, rows_per_day)
    _count_lines_through(monkeypatch, counter)
    writer = _RecordingWriter(counter)

    # Act
    archive_ingest.ingest_months(
        [path], symbol_token=_TOKEN, data_dir=tmp_path / "data", writer=writer
    )

    # Assert 1: 最初の書込は**まだ読み終わっていない**時点で起きる（溜めてから書いていない）。
    assert writer.calls, "1 日も書こうとしていません（検定の前提が壊れています）。"
    first_write_at = writer.calls[0][0]
    assert first_write_at < total_lines

    # Assert 2: その時点は入力の総行数に依存しない定数である
    #   （ヘッダ 1 + 切り落とす先頭日 + 書く最初の日 + 次の日の 1 行目）。
    assert first_write_at == 2 * rows_per_day + 2

    # Assert 3: 1 回の書込が扱う行数も入力 total ではなく「1 日ぶん」で頭打ちになる。
    assert max(rows for _at, rows in writer.calls) == rows_per_day


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
    assert report.rows_read == 2 * _ROWS_PER_MONTH
    assert report.days_written == 6          # 8 UTC 日 − 先頭 1 − 末尾 1
    assert spy.count == 0
    assert not (tmp_path / "data").exists()


# =====================================================================
# E-2. 原子書込は 1 つの公開名（工程 5 🟡-4）
#
# 日次確定（``journal.finalize``）とアーカイブ取り込みは別経路だが、**同じ原子置換**を
# 使わなければならない。片方が private 名を跨いで掴んでいると、所有者側の改名・改修が
# 沈黙のうちに他方を壊す（private は「外から呼ばれない」という約束である）。
# =====================================================================

_PRODUCTION_SOURCES = (
    Path(archive_ingest.__file__),
    Path(journal.__file__),
)


def _references_to(source: Path, symbol: str):
    """``source`` の AST に現れる ``symbol`` の参照を ``(ファイル名, 行)`` で列挙する。"""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    named = [
        node for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == symbol)
        or (isinstance(node, ast.Name) and node.id == symbol)
    ]
    return [(source.name, node.lineno) for node in named]


def test_the_atomic_parquet_write_is_reached_through_a_public_name_only():
    """TC-031: 原子書込は公開名で呼ばれ、private 名の参照が 0 である（AST）。

    grep ではなく AST で見るのは、docstring や注釈の中の綴りを参照と数えないためである。
    """
    # Arrange
    private_refs = [
        ref
        for source in _PRODUCTION_SOURCES
        for ref in _references_to(source, "_write_parquet_atomically")
    ]

    # Act
    public = getattr(journal, "write_parquet_atomically", None)

    # Assert
    assert callable(public), "原子書込の公開名がありません（journal 側の公開が未了）。"
    assert private_refs == [], f"private 名の参照が残っています: {private_refs}"


def test_both_finalize_and_the_archive_ingest_call_the_same_public_name():
    """TC-031b: ``journal.finalize`` と ``archive_ingest`` の双方が同じ公開名を呼ぶ。

    「公開名を足したが片方は private を呼び続けている」を許さない（名前が 2 つある状態は
    単一ソースではない）。
    """
    # Arrange
    def calls_public(source: Path) -> bool:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "write_parquet_atomically":
                return True
            if isinstance(func, ast.Name) and func.id == "write_parquet_atomically":
                return True
        return False

    # Act / Assert
    assert calls_public(Path(journal.__file__))
    assert calls_public(Path(archive_ingest.__file__))


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
    """TC-015 スモーク: 実ファイル 1 件を tmp_path へ取り込み、実測値と一致する。

    ISSUE-447 T1b の「225,381 行 / 22 日」のうち **22 日はラベル日**（``<DATE>`` 列）であり、
    UTC 日 partition の数ではない。ラベル 00:00〜02:59 は UTC では前日に落ちるため、
    UTC 日は 26 になる。両方を固定して取り違えを防ぐ。

    26 UTC 日のうち**書かれるのは 24 日**である（2026-09-02 実測）:
        先頭 ``2020-04-30`` は 9,289 行しかない部分日（月 zip は前月末日の途中から始まる）、
        末尾 ``2020-05-29`` は 9,712 行で開いたまま（次の UTC 日の行は 6 月 zip にある）。
        どちらも「完成品」ではないので書かない（TC-022 / TC-023）。
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

    # Assert: 行数・日数（すべて 2026-09-02 の実測値）
    assert report.rows_read == 225381          # ISSUE-447 実測値
    assert len(label_days) == 22               # ISSUE-447 実測値（ラベル日）
    assert report.head_dropped_day == dt.date(2020, 4, 30)
    assert report.rows_head_dropped == 9289
    assert report.days_carried == 1
    assert report.rows_carried == 9712         # 末尾 UTC 日 2020-05-29
    assert report.rows_written == 225381 - 9289 - 9712
    assert report.rows_unaccounted == 0
    assert report.days_written == 24           # UTC 日 26 − 先頭 1 − 末尾 1
    assert report.days_empty == 4

    # Assert: 木に入った行の総数と、全行が 0.1 格子に載ること（T1b: 225,381/225,381）。
    import pandas as pd

    files = tick_m1.day_parquet_files(
        dt.date(2020, 4, 1), dt.date(2020, 6, 30), symbol=_TOKEN, data_dir=data_dir
    )
    assert len(files) == 24
    frame = pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)
    assert len(frame) == report.rows_written
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
