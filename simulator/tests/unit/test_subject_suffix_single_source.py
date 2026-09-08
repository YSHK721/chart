"""対象接尾辞（Expert / Indicator の受理書式）の宣言が 1 箇所であることを固定する（ISSUE-416）。

何が問題だったか（Phase 8 裁定 T-6 の申し送り）:
    接尾辞の宣言が 2 箇所にあった——(A) 語幹抽出・schema 候補生成が読む
    SUBJECT_SUFFIX（main 層）と、(B) 受理書式検査の正規表現リテラル
    （framework 層 validation の Field pattern）。字形を変えると (A) だけが更新され、
    (B) が旧字形のまま受理検査を続ける。依存方向の制約（framework から main を
    import できない）があるため、宣言そのものを内側（usecase 層）へ移し、
    (A)(B) の双方がそこを参照する。

本モジュールが固定する契約:
  1. 宣言サイトは usecase 層（simulator.usecase.tester_settings）の 1 箇所。
  2. main 層の SUBJECT_SUFFIX は同一オブジェクトの再輸出（既存 import 面の互換維持）。
  3. validation の Field pattern は宣言から導出される（値の一致）。
  4. 機械的検査: `simulator/` 本番コードの**非 docstring** 文字列定数に接尾辞の字形が
     現れるのは宣言サイトだけ（第 2 の宣言が増えたらここが落ちる）。
  5. 受理挙動の characterization: 接尾辞付きは受理・接尾辞なしは拒否
     （導出への置換で挙動が 1 bit も変わらないことの証拠）。

構造: Arrange-Act-Assert。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

#: リポジトリ内の `simulator` パッケージ本体。
_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: 接尾辞の字形を書いてよい唯一のモジュール（`_SIMULATOR_DIR` からの相対 posix パス）。
_ALLOWED_DECLARATION = "usecase/tester_settings/models.py"


def _production_files() -> "list[Path]":
    return sorted(
        p
        for p in _SIMULATOR_DIR.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )


def _docstring_nodes(tree: ast.AST) -> "set[int]":
    """docstring に当たる定数ノードの id 集合（モジュール / クラス / 関数の先頭文）。"""
    nodes: "set[int]" = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                nodes.add(id(body[0].value))
    return nodes


def _suffix_literal_lines(source: str, suffix: str) -> "list[int]":
    """非 docstring の文字列定数に接尾辞の字形が現れる行を列挙する。

    正規表現リテラル（バックスラッシュでエスケープされた字形）も同一視する。
    """
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    needles = (suffix, re.escape(suffix))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and any(needle in node.value for needle in needles)
    ]


class TestSingleDeclarationSite:
    """契約 1・2: 宣言は usecase 層の 1 箇所・main 層は再輸出。"""

    def test_the_usecase_layer_owns_the_declaration(self):
        from simulator.usecase.tester_settings import SUBJECT_SUFFIX

        assert isinstance(SUBJECT_SUFFIX, str) and SUBJECT_SUFFIX

    def test_the_main_layer_reexports_the_same_object(self):
        from simulator.main.tester_settings import ea_input_map
        from simulator.usecase import tester_settings as usecase

        assert ea_input_map.SUBJECT_SUFFIX is usecase.SUBJECT_SUFFIX


class TestValidationDerivesFromTheDeclaration:
    """契約 3・5: pattern は宣言からの導出・受理挙動は不変。"""

    def test_the_field_patterns_equal_the_derived_form(self):
        from simulator.framework.tester_settings import validation
        from simulator.usecase.tester_settings import SUBJECT_SUFFIX

        derived = re.escape(SUBJECT_SUFFIX) + "$"
        patterns = {
            name: next(
                meta.pattern
                for meta in validation._TesterIniModel.model_fields[name].metadata
                if hasattr(meta, "pattern")
            )
            for name in ("Expert", "Indicator")
        }
        assert patterns == {"Expert": derived, "Indicator": derived}

    _REQUIRED = {"Symbol": "JP225", "Period": "M1", "Model": "1"}

    def test_a_suffixed_subject_is_accepted(self):
        from simulator.framework.tester_settings import validation

        model = validation._TesterIniModel(Expert="MA_Slope_EA.ex5", **self._REQUIRED)
        assert model.Expert == "MA_Slope_EA.ex5"

    def test_an_unsuffixed_subject_is_rejected(self):
        from pydantic import ValidationError

        from simulator.framework.tester_settings import validation

        with pytest.raises(ValidationError):
            validation._TesterIniModel(Expert="MA_Slope_EA.mq5", **self._REQUIRED)


class TestNoSecondDeclarationInProductionCode:
    """契約 4: 接尾辞の字形は宣言サイト以外の非 docstring 定数に現れない。"""

    def test_the_literal_exists_only_at_the_declaration_site(self):
        from simulator.usecase.tester_settings import SUBJECT_SUFFIX

        violations = [
            f"{rel}:{line}"
            for path in _production_files()
            for rel in [path.relative_to(_SIMULATOR_DIR).as_posix()]
            if rel != _ALLOWED_DECLARATION
            for line in _suffix_literal_lines(
                path.read_text(encoding="utf-8"), SUBJECT_SUFFIX
            )
        ]
        assert violations == [], (
            "接尾辞の字形の第 2 の宣言が現れました。usecase 層の SUBJECT_SUFFIX から"
            f"導出してください: {violations}"
        )

    def test_the_declaration_site_actually_declares_it(self):
        # 許可サイトが空振り（移動・改名で誰も宣言しない）になっていないこと。
        from simulator.usecase.tester_settings import SUBJECT_SUFFIX

        path = _SIMULATOR_DIR / _ALLOWED_DECLARATION
        assert _suffix_literal_lines(path.read_text(encoding="utf-8"), SUBJECT_SUFFIX)

    def test_the_scan_detects_an_injected_second_declaration(self):
        # 検出力: 通常リテラルと正規表現リテラルの双方を検出する。
        assert _suffix_literal_lines('PATTERN = r"\\.ex5$"\n', ".ex5") == [1]
        assert _suffix_literal_lines('SUFFIX = ".ex5"\n', ".ex5") == [1]

    def test_the_scan_ignores_docstrings(self):
        sample = '"""例: `EA.ex5` の語幹は EA。"""\nX = 1\n'
        assert _suffix_literal_lines(sample, ".ex5") == []
