"""TDD 単体: 依存方向（usecase 3 ファイルの禁止 import 不在・ast 検査）と循環なし
（詳細設計 §6.2.5・条件1/条件2/NFR-OS2）。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_USECASE_FILES = [
    "simulator/usecase/optimize.py",
    "simulator/usecase/optimize_ports.py",
    "simulator/usecase/optimize_strategies.py",
]
_FORBIDDEN = {"simulator.main", "simulator.adapter", "simulator.tools"}


def _imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    return mods


def test_usecase_optimize_modules_do_not_import_pandas():
    # クリーンアーキ依存方向: usecase は pandas を import しない
    for rel in _USECASE_FILES:
        for mod in _imported_modules(_REPO_ROOT / rel):
            assert mod.split(".")[0] != "pandas", f"{rel} imports pandas: {mod}"


def test_usecase_optimize_modules_do_not_import_main_adapter_tools():
    # usecase は simulator.main / adapter / tools を import しない（内向き依存・DIP）
    for rel in _USECASE_FILES:
        for mod in _imported_modules(_REPO_ROOT / rel):
            for forbidden in _FORBIDDEN:
                assert not (
                    mod == forbidden or mod.startswith(forbidden + ".")
                ), f"{rel} imports forbidden module: {mod}"


def test_optimize_strategies_imports_without_circular_dependency():
    # strategies -> optimize（OptimizeError）単方向。import 成功で循環なしを確認（条件2）
    import importlib

    mod = importlib.import_module("simulator.usecase.optimize_strategies")
    assert mod is not None
