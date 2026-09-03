"""アーキ回帰: replay_ui の domain / usecase が numpy / pandas を import しないこと。

domain=依存ゼロ（stdlib のみ）／usecase=domain のみ（偶有的技術は adapter/framework へ隔離）。

2 段で固定する（test_contact_scan_usecase_purity.py 流儀）:
  1. **構造**: ソースを AST 走査し、import 文に numpy / pandas が現れないこと。
  2. **実行**（ISSUE-479 F-7a）: 各モジュールを新しいインタプリタで import し、``sys.modules`` に
     numpy / pandas が現れないこと。AST だけでは**推移的な**流入（依存先の依存が numpy を引く）を
     検出できず、宣言「純・stdlib のみ」が静かに偽になる。実際 domain の forming_bar と usecase の
     causal_compute は stdlib のみの中立核 common.forming_window を import しているだけなのに、
     common パッケージの __init__ が numpy 実装を eager ロードしていたため汚染されていた。
"""
from __future__ import annotations

import ast
import subprocess
import sys
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


# ======================================================================================
# 実行検定（ISSUE-479 F-7a）: AST では見えない**推移的**流入を新しいインタプリタで実測する。
#
# 上の 3 検査は直接 import しか見ない。実際 domain/forming_bar.py・usecase/causal_compute.py は
# 中立核 common.forming_window（stdlib のみ）を import しているが、common/__init__.py が
# numpy 実装を eager import していたため、パッケージ経由で numpy が流入していた。
# 宣言「純・stdlib のみ」が静かに偽になるため、``test_contact_scan_usecase_purity.py:56-92`` と
# 同じ様式で実行時の ``sys.modules`` を固定する。対象は上の列挙から機械導出する（第 2 の表を作らない）。
# ======================================================================================

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _module_name(path: Path) -> str:
    """ファイルパス → import 名（リポジトリ根からの相対パスで機械導出）。"""
    return ".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts)


_RUNTIME_MODULES = [_module_name(p) for p in _DOMAIN_FILES + _USECASE_FILES]


def _leaked_modules(module: str) -> str:
    """新しいインタプリタで ``module`` を import し、流入した禁止モジュール名を返す。"""
    code = (
        "import sys;"
        f"import {module};"
        "leaked=sorted({'numpy','pandas'} & set(sys.modules));"
        "print(','.join(leaked))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"import に失敗: {proc.stderr.strip()[-500:]}"
    return proc.stdout.strip()


@pytest.mark.parametrize("module", _RUNTIME_MODULES, ids=lambda m: m.rsplit(".", 1)[-1])
def test_importing_module_does_not_load_numpy_or_pandas(module):
    """**実行**して固定する: import しただけでは numpy / pandas がロードされない。"""
    leaked = _leaked_modules(module)
    assert not leaked, (
        f"{module} を import しただけで {leaked} がロードされます（推移的な流入）。"
        " 純層の宣言が偽になっています。依存先（common の __init__ 等）の eager import を"
        " 見直してください。"
    )


def test_runtime_check_covers_exactly_the_files_the_ast_check_covers():
    """実行検定の対象は AST 検定の対象から機械導出され、第 2 の列挙表を持たない。"""
    assert _RUNTIME_MODULES == [_module_name(p) for p in _DOMAIN_FILES + _USECASE_FILES]
    assert len(_RUNTIME_MODULES) == len(_DOMAIN_FILES) + len(_USECASE_FILES)


def test_purity_check_issues_one_interpreter_per_module_under_test():
    """計算量テスト: 判定 1 件あたり起動 1 回（発行 − 使用 = 0）。かつ起動数は対象数だけで決まる。

    ``subprocess.run`` を Test Spy で包み、発行した起動回数と、判定に使った結果の個数を突き合わせる。
    回数リテラルは焼き込まない（対象ファイル数から導出する）。
    """
    calls: list = []
    real_run = subprocess.run

    def _spy(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    # 1 点目: 対象 1 件。
    subprocess.run = _spy
    try:
        used_one = [_leaked_modules(_RUNTIME_MODULES[0])]
        issued_one = len(calls)
        calls.clear()
        # 2 点目: 対象を増やす（オーダー表明: 起動は対象数だけで決まり、他の要因で増えない）。
        subset = _RUNTIME_MODULES[:2] if len(_RUNTIME_MODULES) >= 2 else _RUNTIME_MODULES
        used_many = [_leaked_modules(m) for m in subset]
        issued_many = len(calls)
    finally:
        subprocess.run = real_run

    assert issued_one - len(used_one) == 0, "判定 1 件あたり 1 起動を超えています"
    assert issued_many - len(used_many) == 0, "判定 1 件あたり 1 起動を超えています"
    assert issued_many == len(subset), "起動数が対象数以外の要因で増えています"
