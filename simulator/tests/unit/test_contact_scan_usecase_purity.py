"""アーキ回帰: usecase 層（contact_scan サブパッケージ + scan_contacts*）が
numpy / pandas を import しないこと（CLEAN_ARCH: 偶有的技術は adapter/tools へ隔離）。

2 段で固定する（ISSUE-261）:
  1. **構造**: ソースを AST 走査し、import 文に numpy / pandas が現れないこと。
  2. **実行**: 各モジュールを新しいインタプリタで import し、``sys.modules`` に numpy / pandas が
     現れないこと。AST だけでは**推移的な**流入（依存先の依存が pandas を引く）を検出できず、
     宣言「純・stdlib のみ」が静かに偽になる。実際 ``bar_window`` が台帳を参照するようになった際、
     ``marketdata`` パッケージの ``__init__`` が cleaning→outlier_policy 経由で pandas/numpy を
     eager ロードしており、import しただけで純層が汚染される状態だった（同 ISSUE で遅延化）。
"""
from __future__ import annotations

import ast
import subprocess
import sys
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


_REPO_ROOT = Path(__file__).resolve().parents[3]

#: モジュールパス → import 名（実行検定用）。
_MODULES = [
    "simulator.usecase.contact_scan.crossings",
    "simulator.usecase.contact_scan.bar_window",
    "simulator.usecase.contact_scan.spec",
    "simulator.usecase.contact_scan.engine",
    "simulator.usecase.scan_contacts",
    "simulator.usecase.scan_contacts_ports",
]


@pytest.mark.parametrize("module", _MODULES, ids=lambda m: m.rsplit(".", 1)[-1])
def test_importing_usecase_module_does_not_load_numpy_or_pandas(module):
    """**実行**して固定する: import しただけでは numpy / pandas がロードされない。

    AST 走査（上のテスト）は直接 import しか見ないため、依存先の依存が pandas を引く
    「推移的な流入」を検出できない。新しいインタプリタで import し ``sys.modules`` を実測する。
    """
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
    leaked = proc.stdout.strip()
    assert not leaked, (
        f"{module} を import しただけで {leaked} がロードされます（推移的な流入）。"
        " 純層の宣言が偽になっています。依存先（marketdata の __init__ 等）の eager import を"
        " 見直してください。"
    )


def test_bar_window_periods_are_derived_from_the_timeframe_ledger():
    """期間秒は台帳（``marketdata.tf_ledger``）からの導出値であり、第 2 定義を持たない。

    識別力: 手書き dict へ戻す・台帳へ時間足を足して写しが追随しない、のいずれでも Red になる
    （台帳の全キーを網羅比較し、かつソースに数値リテラルの写しが無いことを見る）。
    """
    from marketdata.tf_ledger import TF_BAR_SEC, TF_DESCRIPTORS
    from simulator.usecase.contact_scan import bar_window as mod

    assert mod.TF_SECS is TF_BAR_SEC, "台帳の導出値そのものを参照していません（写しの疑い）"
    assert set(mod.TF_SECS) == set(TF_DESCRIPTORS), "台帳と時間足集合が一致しません"
    for code, d in TF_DESCRIPTORS.items():
        assert mod.TF_SECS[code] == d.bar_sec, f"{code} の期間秒が台帳と食い違います"

    src = (_USECASE / "contact_scan" / "bar_window.py").read_text(encoding="utf-8")
    assert '"1m": 60' not in src and "'1m': 60" not in src, (
        "時間足→秒の手書き dict が復活しています（台帳の第 2 定義・ISSUE-261）"
    )


def test_bar_window_uses_the_ledger_value_for_the_last_bar():
    """末足（次足が無い）の窓長が台帳の ``bar_sec`` と一致する（導出値が実際に使われている）。"""
    from marketdata.tf_ledger import TF_DESCRIPTORS
    from simulator.usecase.contact_scan.bar_window import DAY, bar_window

    t0 = 1786000000
    for code, d in TF_DESCRIPTORS.items():
        if code in ("1W", "1M"):          # 右ラベル: 先頭足が t-dur+DAY 始まり
            start, end = bar_window([t0], 0, code)
            assert end - start == d.bar_sec, f"{code} の窓長が台帳と食い違います"
            assert end == t0 + DAY
        else:                              # 左ラベル: 末足は dur で代用
            start, end = bar_window([t0], 0, code)
            assert (start, end) == (t0, t0 + d.bar_sec), f"{code} の窓長が台帳と食い違います"
