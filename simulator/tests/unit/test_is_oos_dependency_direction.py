"""TDD: run_is_oos.py 依存方向回帰（詳細設計 §5.1 / architecture-executor 条件4）。

usecase→domain のみ。pandas / simulator.main / simulator.adapter を import しない
ことを ast で静的に assert する（クリーンアーキ依存方向の構造的回帰）。
"""
from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_TOP_MODULES = ("pandas", "numpy")
_FORBIDDEN_PREFIXES = ("simulator.main", "simulator.adapter")


def _module_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "usecase"
        / "run_is_oos.py"
    )


def _imported_modules(source: str):
    tree = ast.parse(source)
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                mods.append(node.module)
    return mods


def test_run_is_oos_has_no_forbidden_imports():
    # Arrange
    source = _module_path().read_text(encoding="utf-8")
    mods = _imported_modules(source)
    # Act / Assert: 禁止トップモジュール（pandas/numpy）を import しない
    for m in mods:
        top = m.split(".")[0]
        assert top not in _FORBIDDEN_TOP_MODULES, f"禁止 import 検出: {m}"
    # Assert: simulator.main / simulator.adapter を import しない（依存方向違反禁止）
    for m in mods:
        for pref in _FORBIDDEN_PREFIXES:
            assert not (m == pref or m.startswith(pref + ".")), (
                f"依存方向違反 import 検出: {m}"
            )
