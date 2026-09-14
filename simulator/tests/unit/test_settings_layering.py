"""レイヤ不変条件 I-1〜I-4 の AST 検査（内部設計 §3.3・T-14）。

固定する不変条件:
    I-1: `usecase/tester_settings/*` は pydantic / pandas / numpy を import しない
    I-2: `adapter/tester_settings/*` は pydantic を import しない
    I-3: pydantic の import は `framework/tester_settings/validation.py` の 1 箇所のみ
    I-4: usecase / adapter / framework の `tester_settings` は `simulator.main` を import しない

「制約は機械的検査で担保」（プロジェクト既存合意）に従い、宣言ではなく AST で固定する。
本テストは import の**名前**のみを見るため、対象モジュールの実装完了を待たずに実行できる
（未作成のパッケージは走査対象 0 件となり、作成された時点で自動的に検査対象に入る）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: リポジトリ根（本ファイル = <root>/simulator/tests/unit/... の 3 つ上）。
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 走査対象パッケージ（リポジトリ根からの相対）。
USECASE_PKG = "simulator/usecase/tester_settings"
ADAPTER_PKG = "simulator/adapter/tester_settings"
FRAMEWORK_PKG = "simulator/framework/tester_settings"
MAIN_PKG = "simulator/main/tester_settings"

#: Settings 機能の実装ソース一式（**走査対象の唯一の宣言**）。
#: 宣言を機械的に検査するテスト（無限定の断定の禁止・値の表記規則の単一宣言など）は
#: すべてこの宣言から対象を導く。テストごとにパッケージを書き写すと、層が増えたときに
#: 片方だけが古くなり、新しい層が**沈黙ですり抜ける**（実測: 変換層 `main/tester_settings`
#: が走査対象から漏れ、`ea_input_map.ea_stem` の「例外: なし（全域関数）」が
#: 無限定の断定ガードに掛からなかった）。
SETTINGS_PACKAGES: "tuple[str, ...]" = (USECASE_PKG, ADAPTER_PKG, FRAMEWORK_PKG, MAIN_PKG)

#: パッケージに属さない Settings 機能のソース（例外階層は `domain` 直下の単独ファイル）。
SETTINGS_EXTRA_MODULES: "tuple[str, ...]" = ("simulator/domain/tester_settings_exceptions.py",)

#: I-3 で pydantic の import を唯一許す実装ファイル。
PYDANTIC_ALLOWED_FILE = f"{FRAMEWORK_PKG}/validation.py"

#: I-1 が禁じる外部ライブラリのルート名。
FORBIDDEN_IN_USECASE = ("pydantic", "pandas", "numpy")


def _module_files(package_relpath: str) -> list[Path]:
    """`package_relpath` 配下の `.py` を列挙する（未作成なら空列）。"""
    package_dir = REPO_ROOT / package_relpath
    if not package_dir.is_dir():
        return []
    return sorted(path for path in package_dir.rglob("*.py") if "__pycache__" not in path.parts)


def _imported_module_names(path: Path) -> set[str]:
    """`path` が絶対 import する完全修飾モジュール名の集合を返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _relative_import_levels(path: Path) -> set[int]:
    """`path` の相対 import の階層数（`from .x import y` は 1）の集合を返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.level
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    }


def _roots(module_names: set[str]) -> set[str]:
    """完全修飾名の先頭要素（ルートパッケージ名）の集合。"""
    return {name.split(".", 1)[0] for name in module_names}


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _ids(paths: list[Path]) -> list[str]:
    return [_rel(path) for path in paths]


class TestScanCoverage:
    """走査が空振り（vacuous）していないことを先に固定する（沈黙合格の防止）。"""

    def test_repo_root_is_resolved_correctly(self):
        assert (REPO_ROOT / "simulator" / "usecase").is_dir()

    def test_usecase_package_is_present_and_scanned(self):
        # usecase/tester_settings は実装済みであり、0 件走査は検査の空洞化を意味する
        files = _module_files(USECASE_PKG)
        assert files, f"{USECASE_PKG} に走査対象 .py が存在しない"
        assert {"enums.py", "models.py"} <= {path.name for path in files}

    @pytest.mark.parametrize("package", [USECASE_PKG, ADAPTER_PKG, FRAMEWORK_PKG, MAIN_PKG])
    def test_existing_package_directory_contains_python_files(self, package):
        # 実装フェーズでディレクトリだけ作って中身が無い状態（＝検査対象 0 件）を検出する
        if not (REPO_ROOT / package).is_dir():
            return
        assert _module_files(package), f"{package} はディレクトリが存在するのに .py が 0 件"


class TestInvariantI1:
    """I-1: usecase/tester_settings は pydantic / pandas / numpy を import しない。"""

    def test_usecase_does_not_import_external_libraries(self):
        # Arrange
        files = _module_files(USECASE_PKG)
        # Act: 走査対象が空だと沈黙合格になるため、TestScanCoverage が非空を別途固定している
        violations = {
            _rel(path): sorted(_roots(_imported_module_names(path)) & set(FORBIDDEN_IN_USECASE))
            for path in files
            if _roots(_imported_module_names(path)) & set(FORBIDDEN_IN_USECASE)
        }
        # Assert
        assert violations == {}


class TestInvariantI2:
    """I-2: adapter/tester_settings は pydantic を import しない。"""

    def test_adapter_does_not_import_pydantic(self):
        violations = [
            _rel(path)
            for path in _module_files(ADAPTER_PKG)
            if "pydantic" in _roots(_imported_module_names(path))
        ]
        assert violations == []


class TestInvariantI3:
    """I-3: pydantic の import は framework/tester_settings/validation.py だけ。"""

    def test_pydantic_is_imported_only_by_the_validation_module(self):
        # Arrange
        scanned = (
            _module_files(USECASE_PKG)
            + _module_files(ADAPTER_PKG)
            + _module_files(FRAMEWORK_PKG)
            + _module_files(MAIN_PKG)
        )
        # Act
        importers = sorted(
            _rel(path) for path in scanned if "pydantic" in _roots(_imported_module_names(path))
        )
        # Assert: 0 件（未実装時）または validation.py の 1 件だけ
        assert importers in ([], [PYDANTIC_ALLOWED_FILE]), f"pydantic の import 箇所: {importers}"


class TestInvariantI4:
    """I-4: usecase / adapter / framework の tester_settings は simulator.main を import しない。"""

    @staticmethod
    def _inner_files() -> list[Path]:
        return _module_files(USECASE_PKG) + _module_files(ADAPTER_PKG) + _module_files(FRAMEWORK_PKG)

    def test_inner_layers_do_not_import_simulator_main(self):
        # Arrange / Act
        violations = {
            _rel(path): sorted(
                name
                for name in _imported_module_names(path)
                if name == "simulator.main" or name.startswith("simulator.main.")
            )
            for path in self._inner_files()
        }
        # Assert
        assert {key: value for key, value in violations.items() if value} == {}

    def test_inner_layers_do_not_escape_their_package_via_relative_imports(self):
        # 相対 import で層を越える（`from ...main import x`）と完全修飾名の検査を回避できる。
        # tester_settings パッケージ内では自パッケージ（level==1）までしか許さない。
        violations = {
            _rel(path): sorted(level for level in _relative_import_levels(path) if level > 1)
            for path in self._inner_files()
        }
        assert {key: value for key, value in violations.items() if value} == {}
