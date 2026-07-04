"""アーキ回帰: replay_ui の domain / usecase が numpy / pandas を import しないこと。

domain=依存ゼロ（stdlib のみ）／usecase=domain のみ（偶有的技術は adapter/framework へ隔離）。
ソースを AST 走査して構造的に固定する（test_contact_scan_usecase_purity.py 流儀）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]  # simulator/replay_ui
_DOMAIN = _ROOT / "domain"
_USECASE = _ROOT / "usecase"

_DOMAIN_FILES = sorted(p for p in _DOMAIN.glob("*.py") if p.name != "__init__.py")
_USECASE_FILES = sorted(p for p in _USECASE.glob("*.py") if p.name != "__init__.py")

_FORBIDDEN = {"numpy", "pandas"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize(
    "path", _DOMAIN_FILES + _USECASE_FILES, ids=lambda p: p.name
)
def test_no_numpy_or_pandas_import(path):
    leaked = _imported_roots(path) & _FORBIDDEN
    assert not leaked, f"{path.name} が偶有的技術を import: {leaked}"


@pytest.mark.parametrize("path", _DOMAIN_FILES, ids=lambda p: p.name)
def test_domain_depends_only_on_stdlib(path):
    # domain は依存ゼロ（simulator 他層・外部技術を import しない）。
    roots = _imported_roots(path)
    assert "simulator" not in roots, f"{path.name} が simulator 他層に依存"
    assert not (roots & _FORBIDDEN), f"{path.name} が偶有的技術を import"


@pytest.mark.parametrize("path", _USECASE_FILES, ids=lambda p: p.name)
def test_usecase_depends_only_on_domain_or_stdlib(path):
    # usecase が import する simulator サブモジュールは replay_ui.domain / replay_ui.usecase のみ。
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mod = node.module
            if mod.startswith("simulator."):
                assert mod.startswith("simulator.replay_ui.domain") or mod.startswith(
                    "simulator.replay_ui.usecase"
                ), f"{path.name} が非 domain/usecase 層を import: {mod}"
