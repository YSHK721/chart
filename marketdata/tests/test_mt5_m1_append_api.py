"""M1 追記の公開 API の検定（ISSUE-447 段階 1 / 承認事項 A-5）。

なぜ公開 API を足すのか:
    ``marketdata/mt5_ticks/m1_chain.py`` は M1 CSV の書式を自前で持たないために
    ``tick_m1._format_m1_for_csv``（private）を import していた。private への依存は
    「呼んでよい」と宣言されていない実装詳細への依存であり、権威側が内部を変えた瞬間に
    黙って壊れる。検定 M-3（byte 一致）は壊れたことを**後から**教えるだけで、依存そのものは
    消えない。A-5 の裁定はこの依存を恒久的に解消することであり、そのために
    ``tick_m1`` へ追記の公開 API を 1 個だけ足す（既存関数は 1 行も変えない）。

本検定が固定するのは 4 点である:
    1. 追記結果が全構築経路（``tick_m1.build_m1_from_ticks``）と **1 バイト一致**すること
    2. ヘッダを二重に書かないこと（追記の冪等な入り口）
    3. 空入力で **1 バイトも書かない**こと（新着 0 の周期で書込 0 ＝ CX-b と整合）
    4. ``m1_chain`` が private を**もう参照していない**こと（AST 施行・宣言でなく機械検査）
"""
from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import ingest, m1_chain

_PKG = Path(tick_m1.__file__).resolve().parent / "mt5_ticks"


def _m1(minutes: int, *, start="2026-08-25 09:00") -> pd.DataFrame:
    """``minutes`` 本の M1 バー（date index・OHLCV＋up/dn）。"""
    idx = pd.date_range(pd.Timestamp(start), periods=minutes, freq="min", name="date")
    return pd.DataFrame(
        {
            "open": [66000.0 + i for i in range(minutes)],
            "high": [66010.0 + i for i in range(minutes)],
            "low": [65990.0 + i for i in range(minutes)],
            "close": [66005.0 + i for i in range(minutes)],
            "volume": [20.0] * minutes,
            "up": [9.0] * minutes,
            "dn": [8.0] * minutes,
        },
        index=idx,
    )


# =====================================================================
# 書式の単一規則源であること
# =====================================================================

def test_appending_to_a_new_file_writes_the_loader_compatible_header(tmp_path):
    """不在のファイルへ追記すると、ヘッダ 1 行＋本文が書かれる。"""
    path = tmp_path / "out_m1.csv"

    written = tick_m1.append_m1_rows(_m1(3), path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert written == 3
    assert lines[0] == "date,open,high,low,close,volume,up,dn"
    assert len(lines) == 4


def test_appending_twice_never_repeats_the_header(tmp_path):
    """2 回目以降はヘッダを書かない（追記の入り口が 1 つであることの実証）。"""
    path = tmp_path / "out_m1.csv"

    tick_m1.append_m1_rows(_m1(2), path)
    tick_m1.append_m1_rows(_m1(2, start="2026-08-25 09:02"), path)

    text = path.read_text(encoding="utf-8")
    # di-ok(C2): これは被検査ソースではなく、書き出した M1 CSV（データ）そのものの検査
    assert text.count("date,open") == 1
    assert len(text.splitlines()) == 5


def test_the_appended_bytes_equal_the_whole_build_output(tmp_path):
    """M-3 の主張を公開 API 側でも固定する: 追記結果 == 全構築経路の出力（byte 一致）。

    書式・列順・端数・改行のどれかがずれれば、同じデータが 2 通りの CSV になる。
    """
    m1 = _m1(4)
    appended = tmp_path / "appended_m1.csv"
    whole = tmp_path / "whole_m1.csv"

    tick_m1.append_m1_rows(m1, appended)
    tick_m1._write_m1_csv(m1, whole)

    assert appended.read_bytes() == whole.read_bytes()


def test_an_empty_frame_writes_nothing_at_all(tmp_path):
    """空入力では 1 バイトも書かず、ファイルも作らない（新着 0 の周期で書込 0）。"""
    path = tmp_path / "out_m1.csv"

    written = tick_m1.append_m1_rows(_m1(0), path)

    assert written == 0
    assert not path.exists()


def test_an_empty_frame_leaves_an_existing_file_untouched(tmp_path):
    """既存ファイルがあっても空入力は触らない（mtime を動かす追記も行わない）。"""
    path = tmp_path / "out_m1.csv"
    tick_m1.append_m1_rows(_m1(2), path)
    before = path.read_bytes()

    written = tick_m1.append_m1_rows(_m1(0), path)

    assert written == 0
    assert path.read_bytes() == before


# =====================================================================
# 計算量: 追記へ渡した行数 == CSV に増えた行数（発行 − 使用 = 0）
# =====================================================================

def _data_line_count(path: Path) -> int:
    return max(len(path.read_text(encoding="utf-8").splitlines()) - 1, 0)


@pytest.mark.parametrize("first,second", [(3, 3), (3, 6)])
def test_the_number_of_written_lines_equals_the_number_of_given_bars(tmp_path, first, second):
    """渡したバー数ちょうどが増える（作ってから捨てる行が 0）。

    回数そのものを期待値に焼き込まない。固定するのは「渡した数と増えた数の差が 0」であり、
    入力を 2 点（同数・倍）変えても差が 0 のままであること＝出力量だけで決まることである。
    """
    path = tmp_path / "out_m1.csv"

    tick_m1.append_m1_rows(_m1(first), path)
    grew_first = _data_line_count(path)
    tick_m1.append_m1_rows(_m1(second, start="2026-08-25 10:00"), path)
    grew_second = _data_line_count(path) - grew_first

    assert (grew_first, grew_second) == (first, second)


def test_appending_does_not_read_back_what_is_already_there(tmp_path, monkeypatch):
    """既存分を読み直さない（追記は O(新着)・当日累積に比例しない）。

    既存 CSV を読む経路が生えたら、ここで捕まえる（``pd.read_csv`` の発行が 0）。
    """
    path = tmp_path / "out_m1.csv"
    tick_m1.append_m1_rows(_m1(50), path)
    reads: "list[object]" = []
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: reads.append(a) or pd.DataFrame())

    tick_m1.append_m1_rows(_m1(2, start="2026-08-25 11:00"), path)

    assert reads == []


# =====================================================================
# A-5 の目的: private 依存の恒久解消（AST 施行）
# =====================================================================

def _private_tick_m1_attributes_used_by(filename: str) -> "list[str]":
    """``filename`` が ``tick_m1`` の private 属性を参照している箇所を集める。"""
    tree = ast.parse((_PKG / filename).read_text(encoding="utf-8"))
    return sorted({
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "tick_m1"
        and node.attr.startswith("_")
        and not node.attr.startswith("__")
    })


def test_m1_chain_no_longer_reaches_into_a_private_formatter():
    """A-5: ``m1_chain`` は書式の private 実装を参照しない（公開 API 経由に置換済み）。"""
    assert _private_tick_m1_attributes_used_by("m1_chain.py") == []


def test_m1_chain_calls_the_public_append_api():
    """置換先が実在の公開 API であること（呼んでいる先が消えていないことの実証）。"""
    assert m1_chain.tick_m1.append_m1_rows is tick_m1.append_m1_rows


def test_the_public_api_is_the_only_new_name_added_to_the_authority():
    """A-5 は「1 個だけ足す」承認である。追加は 1 個に閉じる（勝手に面を広げない）。

    ``tick_m1`` の公開名のうち、本スライスで足してよいのは追記 API 1 個だけである。
    """
    public = {
        node.name for node in ast.parse(
            Path(tick_m1.__file__).read_text(encoding="utf-8")
        ).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    # ISSUE-479 M-2: tick 木の権威 5 名は marketdata.tick_tree へ、CLI の main は
    # marketdata.tools.tick_m1_cli へ移した（tick_m1 は同一オブジェクトを再輸出する）。
    # 許容集合から 6 名が抜ける＝**縮小**であり、検定は緩まず強まる。
    assert public == {
        "ts_and_mid", "ticks_to_m1", "m1_csv_path",
        "build_m1_from_ticks", "last_m1_date", "append_m1_from_ticks",
        "forming_bar_from_ticks", "append_m1_rows",
    }


def test_the_ingest_side_still_reaches_the_authority_for_columns():
    """列の権威は依然 ``tick_m1`` である（A-5 で列定義まで動かしていない）。"""
    assert ingest.tick_m1._TICK_COLUMNS == ["timestamp", "bidPrice", "askPrice"]
