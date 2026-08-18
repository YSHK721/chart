"""終了コードの宣言箇所を 1 箇所に固定する（🟡-3 の是正・内部設計 §8.2 / §9.4）。

固定する仕様:
    1. 成功終了コード（`SUCCESS_EXIT_CODE`）・例外翻訳表（`EXIT_CODES`）・翻訳関数
       （`exit_code_for`）は `simulator.main.tester_settings.exit_codes` **だけ**が
       宣言する。他モジュールは import して使う（複製しない）。
    2. `MATH_CALCULATIONS` 経路（`math_calculations`）は生リテラル `0` を返さず、
       共有の `SUCCESS_EXIT_CODE` を読む。

なぜ AST で測るか:
    「値が等しいこと」（`0 == SUCCESS_EXIT_CODE`）は複製が 2 箇所あっても成立する
    ため、**宣言が 1 箇所である**という不変条件を検出できない。宣言（代入）の所在は
    構文木にしか現れないので、構文木を数える。値の一致は別テスト（振る舞い）で測る。

置き場所の制約（循環 import）:
    `run_from_settings` は `math_calculations` を import する。したがって定数を
    `run_from_settings` に置いたまま `math_calculations` から参照すると循環になる。
    共有定数は両者が依存できる下位モジュールに置く必要がある——その構造も本テストが
    `test_the_shared_module_does_not_depend_on_its_users` で固定する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.domain.exceptions import BacktestError, ConfigError
from simulator.main.tester_settings.exit_codes import (
    EXIT_CODES,
    SUCCESS_EXIT_CODE,
    exit_code_for,
)

#: 変換・実行層のパッケージ（本テストの走査対象）。
_PACKAGE_DIR = Path(__import__("simulator.main.tester_settings", fromlist=["__file__"]).__file__).parent

#: 宣言を 1 箇所に集約する先のモジュール名。
_SHARED_MODULE = "exit_codes"


def _modules() -> "dict[str, ast.Module]":
    """パッケージ内の全モジュールを構文木にする（モジュール名 → 構文木）。"""
    return {
        path.stem: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(_PACKAGE_DIR.glob("*.py"))
    }


def _assigned_names(tree: ast.Module) -> "set[str]":
    """モジュール直下で**代入・定義**される名前（import した名前は含めない）。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _declaring_modules(symbol: str) -> "set[str]":
    return {name for name, tree in _modules().items() if symbol in _assigned_names(tree)}


def _loaded_names_in(module_name: str, function_name: str) -> "set[str]":
    """指定関数の本体で**読まれる**名前（`ast.Load`）。"""
    tree = _modules()[module_name]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
            }
    raise AssertionError(f"{module_name}.{function_name} が見つかりません")


class TestSingleDeclarationSite:
    """終了コードの語彙は 1 モジュールだけが宣言する。"""

    @pytest.mark.parametrize("symbol", ["SUCCESS_EXIT_CODE", "EXIT_CODES", "exit_code_for"])
    def test_the_symbol_is_declared_only_in_the_shared_module(self, symbol):
        assert _declaring_modules(symbol) == {_SHARED_MODULE}

    def test_the_math_calculations_path_reads_the_shared_success_code(self):
        # 生リテラル `0` を返していれば、この名前は読まれない
        assert "SUCCESS_EXIT_CODE" in _loaded_names_in("math_calculations", "run_math_calculations")

    def test_the_run_facade_reads_the_shared_success_code(self):
        assert "SUCCESS_EXIT_CODE" in _loaded_names_in("run_from_settings", "run_from_settings")

    def test_the_shared_module_does_not_depend_on_its_users(self):
        """共有モジュールは利用側を import しない（循環 import を作らない）。"""
        tree = _modules()[_SHARED_MODULE]
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        users = {
            "simulator.main.tester_settings.run_from_settings",
            "simulator.main.tester_settings.math_calculations",
        }
        assert imported & users == set()


class TestTranslationBehaviour:
    """公開した語彙が実際にその値・順序で振る舞うこと。"""

    def test_success_is_zero(self):
        assert SUCCESS_EXIT_CODE == 0

    def test_config_error_is_evaluated_before_backtest_error(self):
        # `ConfigError` は `BacktestError` のサブクラス。順序が規約の一部である。
        assert [entry[0] for entry in EXIT_CODES] == [ConfigError, BacktestError]

    def test_config_error_translates_to_two(self):
        assert exit_code_for(ConfigError("x")) == 2

    def test_backtest_error_translates_to_one(self):
        assert exit_code_for(BacktestError("x")) == 1

    def test_an_unrelated_exception_is_re_raised(self):
        # 握り潰さない（未知の失敗を終了コードに化けさせない）
        marker = ValueError("unrelated")
        with pytest.raises(ValueError) as excinfo:
            exit_code_for(marker)
        assert excinfo.value is marker
