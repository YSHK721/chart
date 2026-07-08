"""アーキ回帰: usecase 層（contact_scan サブパッケージ + scan_contacts*）が
numpy / pandas を import しないこと（CLEAN_ARCH: 偶有的技術は adapter/tools へ隔離）。

ソースを AST 走査し、import 文に numpy / pandas が現れないことを構造的に固定する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_USECASE = Path(__file__).resolve().parents[2] / "usecase"

_FILES = [
    _USECASE / "contact_scan" / "crossings.py",
    _USECASE / "contact_scan" / "bar_window.py",
    _USECASE / "contact_scan" / "spec.py",
    _USECASE / "contact_scan" / "engine.py",
    _USECASE / "scan_contacts.py",
    _USECASE / "scan_contacts_ports.py",
]

_FORBIDDEN = {"numpy", "pandas"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_usecase_module_does_not_import_numpy_or_pandas(path):
    assert path.exists(), f"missing: {path}"
    roots = _imported_roots(path)
    leaked = roots & _FORBIDDEN
    assert not leaked, f"{path.name} が偶有的技術を import: {leaked}"
