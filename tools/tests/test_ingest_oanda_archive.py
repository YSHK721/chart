"""``tools/ingest_oanda_archive.py``（合成点）の検定（ISSUE-447 段階 2）。

本 CLI は**規則を持たない**。月の識別・並べ替え・UTC 変換・日分割・書式・パスはすべて
:mod:`marketdata.mt5_ticks.archive_ingest` が持ち、ここは
「どのファイルを・どの木へ・書くか書かないか」を組み立てるだけである。
検定もその境界を固定する（規則の第 2 実装が CLI 側に生えていないこと）。

書込は ``tmp_path`` の下だけ（``data/`` へは 1 バイトも書かない）。
"""
from __future__ import annotations

import ast
import datetime as dt
import zipfile
from pathlib import Path

from marketdata import tick_m1
from marketdata.mt5_ticks import archive_ingest, ingest
from tools import ingest_oanda_archive

_TOKEN = "JP225@OANDA-Japan-MT5-Live"
_HEADER = "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>"


def _month_zip(directory, month: str, specs):
    path = directory / f"ticks_JP225_{month}.zip"
    body = [_HEADER] + [
        f"{label}\t{bid}\t{ask}\t\t".replace(" ", "\t", 1) for label, bid, ask in specs
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"ticks_JP225_{month}.csv", "\n".join(body) + "\n")
    return path


def _source_tree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _month_zip(src, "2020-05", [("2020.05.29 10:00:00.000", 20000.0, 20009.0)])
    _month_zip(src, "2020-06", [("2020.06.10 10:00:00.000", 20010.0, 20019.0)])
    _month_zip(src, "2020-07", [("2020.07.10 10:00:00.000", 20020.0, 20029.0)])
    return src


def test_the_default_token_is_built_by_the_authority_not_written_by_hand():
    """TC-016: 既定トークンは ``ingest.token_for`` の出力（綴りを書き写さない）。

    リテラルの不在は **AST で**確かめる（ソース文字列への grep 検定は禁止・C2）。
    """
    # Arrange
    tree = ast.parse(Path(ingest_oanda_archive.__file__).read_text(encoding="utf-8"))
    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    # Act
    token = ingest_oanda_archive.DEFAULT_SYMBOL_TOKEN

    # Assert: 値は権威が組み立て、綴りは 1 箇所も書かれていない。
    assert token == ingest.token_for("JP225", "OANDA-Japan-MT5-Live")
    assert [text for text in literals if token in text] == [], (
        "トークンの綴りが CLI に第 2 定義として書かれています。"
    )


def test_the_month_range_is_read_by_the_library_not_by_the_cli(tmp_path):
    """TC-020: 月の読み取りは ``archive_ingest.month_key`` が権威（CLI は解釈しない）。"""
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    selected = ingest_oanda_archive.select_months(src, "2020-06:")

    # Assert
    assert [archive_ingest.month_key(p) for p in selected] == ["2020-06", "2020-07"]


def test_months_option_selects_an_inclusive_range(tmp_path):
    """TC-017 境界: ``--months 2020-06:2020-07`` は両端を含み、範囲外を渡さない。"""
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    code = ingest_oanda_archive.main([
        "--src-dir", str(src),
        "--data-dir", str(tmp_path / "data"),
        "--months", "2020-06:2020-07",
    ])

    # Assert: 6 月・7 月だけが木に入る（5 月は 1 日も無い）。
    assert code == 0
    listed = tick_m1.day_parquet_files(
        dt.date(2020, 5, 1), dt.date(2020, 7, 31), symbol=_TOKEN, data_dir=tmp_path / "data"
    )
    assert [p.parts[-4:-1] for p in listed] == [
        ("2020", "06", "10"), ("2020", "07", "10")
    ]


def test_dry_run_writes_nothing(tmp_path):
    """TC-018 境界: ``--dry-run`` は日数・行数を報告するだけで 1 バイトも書かない。"""
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    code = ingest_oanda_archive.main([
        "--src-dir", str(src),
        "--data-dir", str(tmp_path / "data"),
        "--dry-run",
    ])

    # Assert
    assert code == 0
    assert not (tmp_path / "data").exists()


def test_a_full_run_writes_the_days_of_every_month_found(tmp_path):
    """TC-019 正常系: 月を指定しなければ ``--src-dir`` の全月を取り込む。"""
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    code = ingest_oanda_archive.main([
        "--src-dir", str(src), "--data-dir", str(tmp_path / "data"),
    ])

    # Assert
    assert code == 0
    listed = tick_m1.day_parquet_files(
        dt.date(2020, 5, 1), dt.date(2020, 7, 31), symbol=_TOKEN, data_dir=tmp_path / "data"
    )
    assert len(listed) == 3
