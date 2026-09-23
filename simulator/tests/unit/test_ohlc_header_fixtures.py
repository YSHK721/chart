"""共有フィクスチャ `simulator/tests/ohlc_header_fixtures.py` の宣言を機械で確かめる。

なぜ在るか（ISSUE-511 段階 8-D-2・工程 5 レビュー 🟡-3 / 🟡-4 の実測）:
    当該モジュールは「ヘッダと同じ列数の本文」を作ると宣言しながら、区切りを comma で
    固定していた。同じモジュールが宣言するタブ区切りの 2 定数（MT5 エクスポート形式）では
    本文が 2 列になり、**宣言が偽**だった。当時の呼出は comma 系の 1 定数だけだったため
    現に壊れてはいなかったが、次の呼出側がタブ定数を渡せば、列数が合わないだけの本文を
    沈黙で受け取る（読み手は空値を返し、答えは形式上正しく見えるため状態検証では落ちない）。

    宣言を「comma に限る」と狭める文章を書くだけでは、**宣言だけが在って検査が無い**形が
    残る。よって実装を直し、その宣言をここで拘束する。

数え方: 対象はモジュールが宣言する**大文字の文字列定数すべて**（名前を書き写さず
`vars` から導出するので、定数が増えれば自動で対象になる）。時点 2026-09-23・本作業ツリー
では 9 件（うちタブ区切り 2 件）。
"""
from __future__ import annotations

import pytest

from simulator.tests import ohlc_header_fixtures
from simulator.tests.ohlc_header_fixtures import body_rows


def _declared_headers() -> "dict[str, str]":
    """モジュールが宣言するヘッダ定数（名前は書き写さず宣言から導く）。"""
    return {
        name: value
        for name, value in vars(ohlc_header_fixtures).items()
        if name.isupper() and isinstance(value, str)
    }


def _separator_of(header: str) -> str:
    """そのヘッダを割る区切り（タブを含むならタブ・それ以外は comma）。"""
    return "\t" if "\t" in header else ","


def test_the_declared_headers_include_a_tab_separated_one():
    """対象集合が空振りでないことの確認（タブ定数が 1 つも無ければ下の検定は無意味）。"""
    headers = _declared_headers()
    assert headers, "ヘッダ定数が 1 件も導出できていません"
    assert any("\t" in value for value in headers.values()), (
        "タブ区切りのヘッダ定数が無く、区切りの導出を試せていません"
    )


@pytest.mark.parametrize("name", sorted(_declared_headers()))
def test_the_body_has_the_same_column_count_as_its_header(name):
    """本文の列数がヘッダの列数と一致する（宣言「ヘッダと同じ列数の本文」の拘束）。"""
    # Arrange
    header = _declared_headers()[name]
    separator = _separator_of(header)

    # Act
    rows = body_rows(header, 1).splitlines()

    # Assert
    assert len(rows) == 1
    assert len(rows[0].split(separator)) == len(header.split(separator))


@pytest.mark.parametrize("count", [0, 1, 5])
def test_the_body_has_the_requested_number_of_rows(count):
    """要求した行数だけ返す（規模を変える呼出側が数を当てにしている）。"""
    assert len(body_rows(_declared_headers()["MD9"], count).splitlines()) == count
