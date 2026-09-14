"""`simulator.domain.bar` / `bar_time` の依存純度を**実行**して固定する（ISSUE-411 スライス 3）。

両モジュールは「numpy / pandas を直接にも推移的にも import しない」と宣言している
（`bar_time.py` docstring の依存規律・``numpy.datetime64`` は duck typing で判定する）。
スライス 3 で `bar.py` が `bar_time` を import し契約検査を持つようになったため、
この宣言が偽になる経路が 1 本増えた（`bar.py` へ ``import numpy`` を足す／`bar_time` の
判定を ``isinstance(v, np.datetime64)`` へ書き換える、のいずれでも純度が壊れる）。

流儀は `test_contact_scan_usecase_purity.py` と同一である。AST 走査は直接 import しか
見えず、依存先の依存が numpy / pandas を引く「推移的な流入」を検出できないため、
**新しいインタプリタで import し `sys.modules` を実測する**。

なぜ純度が要るか（推測しない・実測済みの理由）:
    `bar_time` の判定表は `Bar.time` の型契約そのものであり、その所有者は domain 層である。
    domain が numpy を要求すると、numpy を持たない実行文脈（軽量 CLI・純ロジックの再利用）
    から domain 契約を読めなくなる。duck typing で判定しているのはこの理由による。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 依存純度を固定する domain モジュール。
_MODULES = ["simulator.domain.bar", "simulator.domain.bar_time"]


@pytest.mark.parametrize("module", _MODULES, ids=lambda m: m.rsplit(".", 1)[-1])
def test_importing_domain_module_does_not_load_numpy_or_pandas(module):
    # Arrange: 新しいインタプリタで当該モジュールだけを import する
    code = (
        "import sys;"
        f"import {module};"
        "leaked=sorted({'numpy','pandas'} & set(sys.modules));"
        "print(','.join(leaked))"
    )
    # Act
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=False,
    )
    # Assert
    assert proc.returncode == 0, f"import に失敗: {proc.stderr.strip()[-500:]}"
    leaked = proc.stdout.strip()
    assert not leaked, (
        f"{module} を import しただけで {leaked} がロードされます（推移的な流入）。"
        " domain の依存規律（numpy / pandas 非依存・numpy.datetime64 は duck typing で判定）"
        " が偽になっています。"
    )


def test_bar_time_contract_is_enforced_without_numpy(tmp_path):
    """契約検査そのものが numpy 非ロードで機能する（純度が飾りでないことの固定）。

    `Bar` の受理・拒否の双方を numpy 未ロードの文脈で観測する。契約検査が numpy に
    依存し始めると、ここで numpy が `sys.modules` に現れて落ちる。
    """
    # Arrange: epoch int は受理・ISO 文字列は ConfigError（numpy を一切 import しない）
    code = (
        "import sys\n"
        "from simulator.domain.bar import Bar\n"
        "from simulator.domain.exceptions import ConfigError\n"
        "Bar(time=1700000000, open=1.0, high=1.5, low=0.8, close=1.2, volume=1.0, spread=1)\n"
        "try:\n"
        "    Bar(time='2024-01-01', open=1.0, high=1.5, low=0.8, close=1.2,"
        " volume=1.0, spread=1)\n"
        "except ConfigError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('未対応の時刻表現が受理された')\n"
        "print(','.join(sorted({'numpy','pandas'} & set(sys.modules))))\n"
    )
    # Act
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=False,
    )
    # Assert
    assert proc.returncode == 0, f"契約検査の実行に失敗: {proc.stderr.strip()[-500:]}"
    assert not proc.stdout.strip(), (
        f"契約検査の実行で {proc.stdout.strip()} がロードされました（domain の依存規律違反）。"
    )
