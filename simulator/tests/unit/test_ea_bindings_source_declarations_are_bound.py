"""`sources` の 2 つの宣言が、その語彙の所有者へ**機械で結ばれている**こと（ISSUE-511 段階 8-B 是正）。

なぜ在るか（工程 5 レビュー 🟡-3 / 🟡-5 の実測）:
    `sources` は「読み方の差を閉じる」ために 2 つの表を持つが、どちらの語彙も**別の
    モジュールが所有者**である。自認のコメントは在ったが、**機械の結びが 0 件**だった
    ——片方を改訂しても赤くならず、同じ名前の別物が 2 つ残る配置である
    （「同じコードを手書き複製するな」: 複製は必ず取り残しを生む）。

    🟡-3 MT5 見出し → 正規化列名の対応表のキーは、MT5 リーダ `ohlc_mt5_csv` が必須と
        する列名の写しである。
    🟡-5 形式 → 読み方の宣言表のキー集合は、形式判定が返す語彙の写しである。結びが
        無いと、形式を 1 つ足したとき読み手の解決は**黙って既定へ倒れる**（registry 用の
        DataFrame 側は DataError で止まるため、両者の扱いが非対称に割れる）。

固定する不変条件:
    1. 正規化する MT5 列名は、すべて MT5 リーダが必須とする列である（部分集合）。
    2. 形式の宣言表のキー集合 ∪ {形式不明} == 形式判定が返す語彙（一致）。

いずれも**片側だけを改訂すると赤くなる**。測り方（AST 抽出器）が空振りしていないことは
自己検査で別に固定する——空集合どうしの一致は「結んだ」ことにならない。
"""
from __future__ import annotations

import ast
from pathlib import Path

from simulator.adapter.repository import ohlc_marketdata_csv, ohlc_mt5_csv
from simulator.main.ea_bindings import sources

#: 形式判定を持つモジュールの実体（語彙の所有者）。
_DETECT_SOURCE = Path(ohlc_marketdata_csv.__file__)
#: 形式判定の関数名。
_DETECT_NAME = "detect_ohlc_form"
#: 「どの形式でもない」を表す戻り値。形式の宣言表には載らない（載せると既定へ倒す
#: 合図が形式の 1 つに化ける）。
_UNKNOWN_FORM = ""


def returned_string_literals(source_path: Path, function_name: str) -> "set[str]":
    """``function_name`` が ``return`` する文字列リテラルの集合（AST の実測）。

    実行して集めない理由: 到達しなかった枝の語彙を取り逃すため。**書かれている語彙**を
    そのまま読む（docstring の説明文は Return 節点ではないので混ざらない）。
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    return {
        node.value.value
        for node in ast.walk(target)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


class TestTheMt5ColumnNamesAreBoundToTheReader:
    """🟡-3: 正規化する MT5 列名の所有者は MT5 リーダである。"""

    def test_every_normalised_mt5_column_is_required_by_the_mt5_reader(self):
        """正規化表のキー ⊆ MT5 リーダの必須列。

        片方の綴りを変えると（例: 見出し <OPEN> を <OPENING> にする）ここが落ちる。
        部分集合にするのは、リーダが Bar へ写すために要る列（<TIME> 等）が registry の
        正規化対象とは限らないからである。
        """
        # Arrange / Act
        normalised = set(sources._MT5_COLUMN_NAMES)
        required = set(ohlc_mt5_csv._REQUIRED)
        # Assert
        assert normalised <= required, sorted(normalised - required)

    def test_neither_side_of_the_binding_is_empty(self):
        """空集合どうしの包含で緑になっていないこと（結びの自己検査）。"""
        # Assert
        assert len(sources._MT5_COLUMN_NAMES) > 0
        assert len(ohlc_mt5_csv._REQUIRED) > 0

    def test_the_spread_column_is_on_both_sides(self):
        """段階 8-B が足した気配幅の列が、両側に在ること（結びが効く対象の実証）。"""
        # Assert
        assert "<SPREAD>" in sources._MT5_COLUMN_NAMES
        assert "<SPREAD>" in ohlc_mt5_csv._REQUIRED


class TestTheFormVocabularyHasOneOwner:
    """🟡-5: 形式の語彙の所有者は形式判定の関数である。"""

    def test_the_declared_forms_match_the_vocabulary_detect_returns(self):
        """形式の宣言表のキー ∪ {形式不明} == 判定が返す語彙。

        どちらか片側に形式を足すと落ちる。落ちなければ、足した形式に対して読み手の
        解決が黙って既定リーダへ倒れる配置が素通りする。
        """
        # Arrange / Act
        declared = set(sources._FORMS) | {_UNKNOWN_FORM}
        detected = returned_string_literals(_DETECT_SOURCE, _DETECT_NAME)
        # Assert
        assert declared == detected, {
            "宣言のみ": sorted(declared - detected),
            "判定のみ": sorted(detected - declared),
        }

    def test_the_unknown_form_is_not_a_declared_form(self):
        """形式不明は宣言表の要素ではない（既定へ倒す合図であって形式ではない）。"""
        # Assert
        assert _UNKNOWN_FORM not in sources._FORMS

    def test_the_extractor_reads_return_literals(self, tmp_path):
        """AST 抽出器が実際に戻り値の語彙を拾えること（測り方の自己検査）。

        抽出器が常に空集合を返すなら、上の一致は「空 == 空」で緑になり主張が空洞化する。
        """
        # Arrange
        sample = tmp_path / "sample.py"
        sample.write_text(
            'def f(x):\n'
            '    """return "docstring は対象外" """\n'
            '    if x:\n'
            '        return "alpha"\n'
            '    return ""\n',
            encoding="utf-8",
        )
        # Act
        measured = returned_string_literals(sample, "f")
        # Assert
        assert measured == {"alpha", ""}
