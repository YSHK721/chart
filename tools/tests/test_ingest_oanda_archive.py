"""``tools/ingest_oanda_archive.py``（合成点）の検定（ISSUE-447 段階 2）。

本 CLI は**規則を持たない**。月の識別・並べ替え・UTC 変換・日分割・書式・パスはすべて
:mod:`marketdata.mt5_ticks.archive_ingest` が持ち、ここは
「どのファイルを・どの木へ・書くか書かないか」を組み立てるだけである。
検定もその境界を固定する（規則の第 2 実装が CLI 側に生えていないこと）。

書込は ``tmp_path`` の下だけ（``data/`` へは 1 バイトも書かない）。
"""
from __future__ import annotations

import ast
import dataclasses
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


def _days_of(month: str, base: float):
    """1 か月 3 UTC 日（09 / 10 / 11 日・各 1 行）。

    3 日にしてあるのは、走査範囲の端（先頭 1 日・末尾 1 日）が書かれないためである
    （``test_mt5_archive_ingest.py`` TC-022 / TC-023）。1 日だけの月を並べると、どの月からも
    書かれる日が無くなり、CLI の合成が正しいかを何も表明しない検定になる。
    """
    year, _, mm = month.partition("-")
    return [
        (f"{year}.{mm}.{day:02d} 10:00:00.000", base + i, base + i + 9.0)
        for i, day in enumerate((9, 10, 11))
    ]


def _source_tree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _month_zip(src, "2020-05", _days_of("2020-05", 20000.0))
    _month_zip(src, "2020-06", _days_of("2020-06", 20010.0))
    _month_zip(src, "2020-07", _days_of("2020-07", 20020.0))
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
    #   6/9 は走査の先頭 UTC 日＝切り落とし、7/11 は末尾＝持ち越しなので書かれない。
    assert code == 0
    listed = tick_m1.day_parquet_files(
        dt.date(2020, 5, 1), dt.date(2020, 7, 31), symbol=_TOKEN, data_dir=tmp_path / "data"
    )
    assert [p.parts[-4:-1] for p in listed] == [
        ("2020", "06", "10"), ("2020", "06", "11"),
        ("2020", "07", "09"), ("2020", "07", "10"),
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


def test_a_bare_dukascopy_token_is_rejected(tmp_path):
    """TC-032 異常系: ``--symbol-token`` に区切りが無い指定は拒否する（exit 2）。

    銘柄だけのトークンは**既存 Dukascopy 木の銘柄そのもの**である。区切りを必須にすること
    で、既存木へ書き込む指定がコマンド行の段階で止まる（工程 5 🔴-3）。
    """
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    code = ingest_oanda_archive.main([
        "--src-dir", str(src),
        "--data-dir", str(tmp_path / "data"),
        "--symbol-token", "JP225",
    ])

    # Assert: 1 バイトも書かずに落ちる。
    assert code == 2
    assert not (tmp_path / "data").exists()


def test_a_token_with_the_separator_is_accepted(tmp_path):
    """TC-033 正常系: 区切りを含むトークンはそのまま通る（既定トークンも含む）。"""
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    code = ingest_oanda_archive.main([
        "--src-dir", str(src),
        "--data-dir", str(tmp_path / "data"),
        "--symbol-token", ingest_oanda_archive.DEFAULT_SYMBOL_TOKEN,
    ])

    # Assert
    assert code == 0
    assert len(tick_m1.day_parquet_files(
        dt.date(2020, 5, 1), dt.date(2020, 7, 31), symbol=_TOKEN, data_dir=tmp_path / "data"
    )) == 7


def test_each_month_is_reported_on_its_own_log_line(tmp_path, caplog):
    """TC-034: 月ごとに 1 行報告する（月・読み行・書込日・持ち越し）。

    76 か月の一括実行は数十分かかる。終わってから 1 行だけ出す報告は、途中で落ちたときに
    「どこまで進んだか」を何も残さない。
    """
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    with caplog.at_level("INFO", logger=ingest_oanda_archive.LOG.name):
        code = ingest_oanda_archive.main([
            "--src-dir", str(src), "--data-dir", str(tmp_path / "data"),
        ])

    # Assert: 3 か月ぶんの行が、月の綴りを含んで 1 行ずつ出ている。
    assert code == 0
    #   総括行にも月の綴りは出るので、行頭が月であることで数える。
    lines = [r.getMessage() for r in caplog.records]
    for month in ("2020-05", "2020-06", "2020-07"):
        own = [text for text in lines if text.startswith(month + ":")]
        assert len(own) == 1, f"{month} の行が {len(own)} 本あります: {lines}"
        assert "持ち越し" in own[0] and "書込日" in own[0] and "読み" in own[0]


def test_unaccounted_rows_make_the_command_fail(tmp_path, monkeypatch):
    """TC-035 異常系: 行方の説明がつかない行が 1 行でもあれば非 0 で終わる。

    「読んだ行の行方が全部書いてある」は報告の飾りではなく通過条件である。0 でない値を
    出したまま exit 0 で終わると、捨てた行が正常終了の中に埋もれる。
    """
    # Arrange: 取り込み自体は成功させ、報告だけ辻褄の合わない値にする。
    src = _source_tree(tmp_path)
    real = archive_ingest.ingest_months

    def leaking(*args, **kwargs):
        report = real(*args, **kwargs)
        return dataclasses.replace(report, rows_read=report.rows_read + 1)

    monkeypatch.setattr(archive_ingest, "ingest_months", leaking)

    # Act
    code = ingest_oanda_archive.main([
        "--src-dir", str(src), "--data-dir", str(tmp_path / "data"),
    ])

    # Assert
    assert code != 0


def test_a_single_month_without_the_separator_selects_only_that_month(tmp_path):
    """TC-036 境界: ``--months 2020-06`` は**単月**（``2020-06:`` と同義にしない）。

    区切りの無い指定を「開始だけ」と読むと、1 か月だけ入れるつもりの指示が以降の全月を
    取り込む。指定の見た目と結果の落差が大きく、取り消しが利かない側へ倒れる。
    """
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    selected = ingest_oanda_archive.select_months(src, "2020-06")

    # Assert
    assert [archive_ingest.month_key(p) for p in selected] == ["2020-06"]


def test_a_full_run_writes_the_days_of_every_month_found(tmp_path):
    """TC-019 正常系: 月を指定しなければ ``--src-dir`` の全月を取り込む。"""
    # Arrange
    src = _source_tree(tmp_path)

    # Act
    code = ingest_oanda_archive.main([
        "--src-dir", str(src), "--data-dir", str(tmp_path / "data"),
    ])

    # Assert: 9 UTC 日のうち先頭（5/9）と末尾（7/11）を除く 7 日が書かれる。
    assert code == 0
    listed = tick_m1.day_parquet_files(
        dt.date(2020, 5, 1), dt.date(2020, 7, 31), symbol=_TOKEN, data_dir=tmp_path / "data"
    )
    assert len(listed) == 7
