"""終了コードの宣言箇所を 1 箇所に固定する（🟡-3 の是正・A-6・内部設計 §8.2 / §9.4）。

固定する仕様:
    1. 成功終了コード（`SUCCESS_EXIT_CODE`）・例外翻訳表（`EXIT_CODES`）・翻訳関数
       （`exit_code_for`）は `simulator.adapter.exit_codes` **だけ**が宣言する。
       `simulator` 配下（テストを除く）の他モジュールは import して使う（複製しない）。
    2. `simulator.main.tester_settings.exit_codes` は宣言を持たず、再輸出だけを行う
       （既存呼出側 `run_from_settings` / `math_calculations` の import 経路を保つ）。
    3. 成功終了コードを返す地点（A-1 以降は実行段 `run_effective_settings` の 1 箇所）は
       生リテラル `0` を返さず、共有の `SUCCESS_EXIT_CODE` を読む。実行の入口
       （`run_from_settings` / `run_math_calculations`）は終了コードのリテラルを持たない。

A-6 で宣言を main → adapter へ移した理由:
    翻訳規約は `adapter/controller.py`（`BacktestController.run`）も使う。宣言が
    main 層にあるまま controller から委譲すると adapter → main の import が生じ、
    `controller.py` 自身の宣言「adapter 層は usecase + domain のみに依存する
    （framework / main は import しない）」に反する。内側 4 層から `simulator.main`
    への import が 0 件であることは `test_layer_dependency_direction.py` が固定する。

なぜ AST で測るか:
    「値が等しいこと」（`0 == SUCCESS_EXIT_CODE`）は複製が 2 箇所あっても成立する
    ため、**宣言が 1 箇所である**という不変条件を検出できない。宣言（代入）の所在は
    構文木にしか現れないので、構文木を数える。値の一致は別テスト（振る舞い）で測る。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.adapter.exit_codes import (
    EXIT_CODES,
    SUCCESS_EXIT_CODE,
    exit_code_for,
)
from simulator.domain.exceptions import BacktestError, ConfigError

#: `simulator` パッケージ本体。
_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: 宣言を集約する先（唯一の宣言場所）。
_DECLARING_FILE = _SIMULATOR_DIR / "adapter" / "exit_codes.py"

#: 変換・実行層のパッケージ（宣言 0 件であることを固定する対象）。
_SETTINGS_PACKAGE_DIR = _SIMULATOR_DIR / "main" / "tester_settings"

#: 単一宣言を固定する記号。
_SYMBOLS = ("SUCCESS_EXIT_CODE", "EXIT_CODES", "exit_code_for")


def _production_modules() -> "list[Path]":
    """`simulator` 配下の本番モジュール（テスト・`__pycache__` を除く）。"""
    return sorted(
        path
        for path in _SIMULATOR_DIR.rglob("*.py")
        if "__pycache__" not in path.parts and "tests" not in path.parts
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


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


def _declaring_files(symbol: str) -> "set[Path]":
    """本番コードのうち、その記号を**宣言している**ファイル。"""
    return {
        path
        for path in _production_modules()
        if symbol in _assigned_names(_tree(path))
    }


def _settings_package_modules() -> "dict[str, ast.Module]":
    """`main/tester_settings` 直下の全モジュール（モジュール名 → 構文木）。"""
    return {path.stem: _tree(path) for path in sorted(_SETTINGS_PACKAGE_DIR.glob("*.py"))}


def _loaded_names_in(module_name: str, function_name: str) -> "set[str]":
    """指定関数の本体で**読まれる**名前（`ast.Load`）。"""
    tree = _settings_package_modules()[module_name]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
            }
    raise AssertionError(f"{module_name}.{function_name} が見つかりません")


def _imported_modules(tree: ast.Module) -> "set[str]":
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }


class TestSingleDeclarationSite:
    """終了コードの語彙は本番コード全体で 1 ファイルだけが宣言する。"""

    @pytest.mark.parametrize("symbol", _SYMBOLS)
    def test_the_symbol_is_declared_only_in_the_adapter_module(self, symbol):
        assert _declaring_files(symbol) == {_DECLARING_FILE}

    @pytest.mark.parametrize("symbol", _SYMBOLS)
    def test_the_settings_package_declares_nothing(self, symbol):
        # 移設後の不変条件: main/tester_settings 側の宣言は 0 件（再輸出のみ）。
        declaring = {
            name
            for name, tree in _settings_package_modules().items()
            if symbol in _assigned_names(tree)
        }
        assert declaring == set()

    def test_the_settings_module_re_exports_the_adapter_declaration(self):
        # 既存呼出側の import 経路（main.tester_settings.exit_codes）を保つ。
        tree = _settings_package_modules()["exit_codes"]
        assert "simulator.adapter.exit_codes" in _imported_modules(tree)

    @pytest.mark.parametrize("symbol", _SYMBOLS)
    def test_the_re_export_is_the_same_object_as_the_declaration(self, symbol):
        # 再輸出が「値の写し」ではなく同一実体であること（複製の混入を排す）。
        import simulator.adapter.exit_codes as declared
        import simulator.main.tester_settings.exit_codes as re_exported

        assert getattr(re_exported, symbol) is getattr(declared, symbol)

    def test_the_execution_stage_reads_the_shared_success_code(self):
        # 生リテラル `0` を返していれば、この名前は読まれない。
        # A-1（ISSUE-397）で成功終了コードを返す地点は実行段 1 箇所に集約された。
        assert "SUCCESS_EXIT_CODE" in _loaded_names_in(
            "run_from_settings", "run_effective_settings"
        )

    def test_the_run_facade_reads_the_shared_translation(self):
        # facade は失敗を終了コードへ翻訳する。翻訳表を自前で持たない。
        assert "exit_code_for" in _loaded_names_in("run_from_settings", "run_from_settings")

    @pytest.mark.parametrize(
        "module_name,function_name",
        [
            ("run_from_settings", "run_from_settings"),
            ("math_calculations", "run_math_calculations"),
        ],
    )
    def test_the_entry_points_do_not_mint_exit_codes(self, module_name, function_name):
        """入口は実行段へ委譲するだけで、終了コードの整数リテラルを持たない。

        A-1 の一本化の検査でもある: 入口が自前の終了コードを持てば、そこに 2 本目の
        実行経路が生えている（値が同じでも経路は 2 本になる）。
        """
        tree = _settings_package_modules()[module_name]
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                literals = {
                    child.value
                    for child in ast.walk(node)
                    if isinstance(child, ast.Constant) and isinstance(child.value, int)
                }
                assert literals == set()
                return
        raise AssertionError(f"{module_name}.{function_name} が見つかりません")

    def test_the_declaring_module_does_not_depend_on_its_users(self):
        """宣言モジュールは利用側を import しない（循環 import を作らない）。"""
        imported = _imported_modules(_tree(_DECLARING_FILE))
        users = {
            "simulator.adapter.controller",
            "simulator.main.tester_settings.exit_codes",
            "simulator.main.tester_settings.run_from_settings",
            "simulator.main.tester_settings.math_calculations",
        }
        assert imported & users == set()

    def test_the_declaring_module_depends_only_on_domain_exceptions(self):
        """adapter 層の依存規律: usecase + domain のみ（framework / main は不可）。"""
        project_imports = {
            module
            for module in _imported_modules(_tree(_DECLARING_FILE))
            if module.split(".")[0] == "simulator"
        }
        assert project_imports == {"simulator.domain.exceptions"}


class TestTheControllerDoesNotKeepItsOwnTable:
    """`BacktestController.run` が翻訳表を再実装していないこと（A-6）。"""

    def _run_function(self) -> ast.FunctionDef:
        tree = _tree(_SIMULATOR_DIR / "adapter" / "controller.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run":
                return node
        raise AssertionError("BacktestController.run が見つかりません")

    def test_run_returns_no_bare_exit_code_literals(self):
        # `return 0` / `return 1` / `return 2` が残っていれば表が 2 箇所ある。
        literals = [
            node.value.value
            for node in ast.walk(self._run_function())
            if isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ]
        assert literals == []

    def test_run_delegates_to_the_shared_translation(self):
        called = {
            node.func.id
            for node in ast.walk(self._run_function())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "exit_code_for" in called


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
