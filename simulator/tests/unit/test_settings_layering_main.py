"""不変条件 I-6 の AST 検査: 変換層は `simulator.sim_ui` を import しない。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
検査対象 `simulator/main/tester_settings/*` が未作成の間は走査対象 0 件になるため、
`TestScanCoverage` が「作られたのに空」の状態だけを落とす（沈黙合格を作らない）。

I-6 を足す理由（実測）:
    既存 I-4（`test_settings_layering.py`）は usecase / adapter / framework の
    `tester_settings` のみを対象とし、**変換層（`main/tester_settings`）から
    `sim_ui` への import を検出できない**。実測では
      - `simulator/main/**` → `simulator.sim_ui` の参照は **0 件**
      - `simulator/sim_ui/**` → `simulator.main` の参照は複数（`composition_root_jobs.py`
        `run_job.py` `symbol_spec_catalog.py` ほか）
    であり、変換層が `sim_ui`（`RunProfile` / `SymbolSpecCatalog`）を掴むと
    `main` ⇄ `sim_ui` のパッケージ循環になる。よって銘柄仕様は既存の
    `simulator.usecase.models.SymbolSpec` で受け渡す（`EngineBinding.symbol_spec`）。

本モジュールは既存 `test_settings_layering.py` の AST 補助を**import して再利用**する
（同じ走査コードを書き写さない＝プロジェクト規約「同じコードを手書き複製するな」。
かつ既存ファイルを 1 行も改変しない＝内部設計 §9.4 G-1）。
"""
from __future__ import annotations

from pathlib import Path

from simulator.tests.unit.test_settings_layering import (
    MAIN_PKG,
    REPO_ROOT,
    _imported_module_names,
    _module_files,
    _rel,
    _relative_import_levels,
    _roots,
)

#: I-6 が禁じる依存先（パッケージ循環の相手側）。
FORBIDDEN_IN_MAIN_TESTER_SETTINGS = "simulator.sim_ui"


class TestScanCoverage:
    """走査の空洞化を先に塞ぐ（沈黙合格の防止）。"""

    def test_main_package_directory_is_not_empty_when_present(self):
        if not (REPO_ROOT / MAIN_PKG).is_dir():
            return
        assert _module_files(MAIN_PKG), f"{MAIN_PKG} はディレクトリが存在するのに .py が 0 件"


class TestInvariantI6:
    """I-6: `main/tester_settings/*` は `simulator.sim_ui` を import しない。"""

    def test_conversion_layer_does_not_import_sim_ui(self):
        # Arrange
        files = _module_files(MAIN_PKG)
        # Act
        violations = {
            _rel(path): sorted(
                name
                for name in _imported_module_names(path)
                if name == FORBIDDEN_IN_MAIN_TESTER_SETTINGS
                or name.startswith(f"{FORBIDDEN_IN_MAIN_TESTER_SETTINGS}.")
            )
            for path in files
        }
        # Assert
        assert {key: value for key, value in violations.items() if value} == {}

    def test_conversion_layer_does_not_import_pydantic_or_pandas(self):
        # I-3 の裏返し: 変換層は pydantic を直接掴まない（検証層の内側に閉じる）。
        forbidden = {"pydantic", "pandas"}
        violations = {
            _rel(path): sorted(_roots(_imported_module_names(path)) & forbidden)
            for path in _module_files(MAIN_PKG)
            if _roots(_imported_module_names(path)) & forbidden
        }
        assert violations == {}

    def test_conversion_layer_does_not_escape_its_package_via_relative_imports(self):
        # 相対 import（`from ...sim_ui import x`）で完全修飾名の検査を回避させない。
        violations = {
            _rel(path): sorted(level for level in _relative_import_levels(path) if level > 1)
            for path in _module_files(MAIN_PKG)
        }
        assert {key: value for key, value in violations.items() if value} == {}


class TestSimUiIsTheOneThatDependsOnMain:
    """依存の向きの実測固定（I-6 の根拠が消えたら落ちる）。"""

    @staticmethod
    def _python_files(package_relpath: str) -> list[Path]:
        package_dir = REPO_ROOT / package_relpath
        return sorted(
            path
            for path in package_dir.rglob("*.py")
            if "__pycache__" not in path.parts and "/tests/" not in path.as_posix()
        )

    def test_sim_ui_imports_simulator_main(self):
        # この向きが存在するからこそ、逆向き（main → sim_ui）が循環になる
        importers = [
            _rel(path)
            for path in self._python_files("simulator/sim_ui")
            if any(
                name == "simulator.main" or name.startswith("simulator.main.")
                for name in _imported_module_names(path)
            )
        ]
        assert importers, "sim_ui → simulator.main の参照が消えた（I-6 の根拠を再確認すること）"
