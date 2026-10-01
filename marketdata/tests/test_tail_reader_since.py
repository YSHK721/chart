"""日時で切る逆シーク（:func:`marketdata.tail_reader.tail_bytes_since`）の境界（ISSUE-534）。

用語（初出定義）:
    窓の開始位置
        ＝ date 列が ``since`` 以降になる最初のデータ行が始まるバイト位置。修復
          （``marketdata.tick_m1.heal_m1_days_for_series``）はこの位置から末尾までを素材で
          置き換える。位置を 1 行でも読み違えると、**履歴の行を巻き添えで消す**か、**直すべき行を
          残す**かのどちらかになる。
    不変条件
        ＝ 返った ``(位置, バイト列)`` について、バイト列がファイルの当該位置以降と一致すること。
          これが崩れると、突合の対象と書き直す対象が別物になる。

なぜこの検定が要るか（想定失敗分析・2026-09-26）:
    この関数は修復が唯一の書き直し位置を決める場所であり、失敗は**データの欠落**として現れる
    （検査では落ちず、CSV は連続して見える）。ブロック境界・末尾の改行欠け・該当行 0 件は
    どれも実ファイルで起きる形なので、実装の分岐ではなく**入力の形**で網羅する。

    記録（TDD の順序についての正直な申し送り）: 本ファイルの検定は step S-4（Green）で
    書いた実装に対して**後から**足したものであり、**Red を観測していない**（原因分類
    ① 過剰実装＝最小実装を超えて境界を書いた）。撤去でなく検定の追加を選んだのは、境界の
    取り違えがデータ欠落として現れるためである。検出力は変異試験で実測した（報告に記載）。

本検定が固定するもの:
  T-1 不変条件（返ったバイト列＝ファイルの当該位置以降）が、入力の形に依らず成り立つ。
  T-2 該当行が 1 つも無ければ位置はファイル長・バイト列は空（呼出側はそこへ追記すれば済む）。
  T-3 全データ行が該当すればヘッダ直後から返る（履歴を読み飛ばさない）。
  T-4 ヘッダのみ・空ファイルでも落ちない。
  T-5 末尾の改行が無い行（torn 書込）でも位置を取り違えない。
  T-6 逆シークのブロック境界を跨いでも同じ答えになる（境界に依存しない）。

書込はすべて ``tmp_path``。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from marketdata import tail_reader

_HEADER = b"date,open,high,low,close,volume\n"


def _row(day: str) -> bytes:
    return f"{day} 00:00:00,1.0,1.0,1.0,1.0,1.0\n".encode("utf-8")


def _file(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "m1.csv"
    path.write_bytes(content)
    return path


#: 3 日ぶんのデータ行を持つ CSV（実バイト）。
_THREE_DAYS = _HEADER + _row("2026-09-01") + _row("2026-09-02") + _row("2026-09-03")


@pytest.mark.parametrize("block", [16, 64 * 1024])
@pytest.mark.parametrize(
    "content,since,expect_first",
    [
        (_THREE_DAYS, "2026-09-02", b"2026-09-02"),        # 中間から
        (_THREE_DAYS, "2026-08-01", b"2026-09-01"),        # 全行が該当（T-3）
        (_THREE_DAYS, "2026-09-03", b"2026-09-03"),        # 末尾 1 行だけ
        (_HEADER + _row("2026-09-05"), "2026-09-05", b"2026-09-05"),  # データ 1 行
        (_THREE_DAYS[:-1], "2026-09-02", b"2026-09-02"),   # 末尾の改行なし（T-5）
        (_THREE_DAYS[:-1], "2026-09-03", b"2026-09-03"),   # 末尾の改行なし・最終行が該当
    ],
)
def test_t1_the_returned_bytes_are_exactly_the_file_from_the_returned_offset(
    tmp_path, monkeypatch, block, content, since, expect_first
):
    """T-1 / T-3 / T-5 / T-6: 不変条件が入力の形とブロック境界に依らず成り立つ。"""
    # Arrange
    monkeypatch.setattr(tail_reader, "_BLOCK_SIZE", block)
    path = _file(tmp_path, content)

    # Act
    offset, data = tail_reader.tail_bytes_since(path, since)

    # Assert
    assert data == content[offset:]
    assert data.startswith(expect_first)
    assert content[:offset].endswith(b"\n")  # 位置は必ず行境界である。


@pytest.mark.parametrize("block", [16, 64 * 1024])
def test_t2_no_matching_row_returns_the_end_of_the_file_and_no_bytes(
    tmp_path, monkeypatch, block
):
    """T-2: 該当行が無ければ位置はファイル長・バイト列は空（そこへ追記すれば済む）。"""
    # Arrange
    monkeypatch.setattr(tail_reader, "_BLOCK_SIZE", block)
    path = _file(tmp_path, _THREE_DAYS)

    # Act
    offset, data = tail_reader.tail_bytes_since(path, "2026-10-01")

    # Assert
    assert (offset, data) == (len(_THREE_DAYS), b"")


@pytest.mark.parametrize("content", [_HEADER, b""])
def test_t4_a_file_without_data_rows_is_answered_without_failing(tmp_path, content):
    """T-4: ヘッダのみ・空ファイルでも落ちず、位置はファイル長・バイト列は空である。"""
    # Arrange
    path = _file(tmp_path, content)

    # Act
    offset, data = tail_reader.tail_bytes_since(path, "2026-09-02")

    # Assert
    assert (offset, data) == (len(content), b"")


def test_t3_the_offset_never_points_into_the_header(tmp_path):
    """T-3（補）: 全行が該当してもヘッダは含めない（ヘッダを二重に書かせない）。"""
    # Arrange
    path = _file(tmp_path, _THREE_DAYS)

    # Act
    offset, data = tail_reader.tail_bytes_since(path, "1970-01-01")

    # Assert
    assert offset == len(_HEADER)
    assert not data.startswith(b"date,")
