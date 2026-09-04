"""TDD: 週次戦略 usecase 依存方向回帰（詳細設計 §2.2 DI-1 / §9.9）。

usecase は domain と自層 Port のみ依存。pandas/numpy・simulator.adapter/framework/main
を import しないことを ast で静的に assert する。
"""
from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_TOP = ("pandas", "numpy")
_FORBIDDEN_PREFIXES = ("simulator.main", "simulator.adapter", "simulator.framework")
_MODULES = (
    "run_weekly_segments",
    "vol_band_ports",
    "validation_ports",
)


def _usecase_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "usecase"


def _imported(source: str):
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


def test_weekly_usecase_modules_have_no_forbidden_imports():
    for name in _MODULES:
        path = _usecase_dir() / f"{name}.py"
        mods = _imported(path.read_text(encoding="utf-8"))
        for m in mods:
            top = m.split(".")[0]
            assert top not in _FORBIDDEN_TOP, f"{name}: 禁止 import {m}"
            for pref in _FORBIDDEN_PREFIXES:
                assert not (m == pref or m.startswith(pref + ".")), (
                    f"{name}: 依存方向違反 import {m}"
                )
