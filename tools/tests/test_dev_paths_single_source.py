"""パス解決の唯一源（``tools/dev_paths.txt``）と 3 消費者の一致を強制する（ISSUE-279）。

なぜ必要か:
    import パスの定義が「venv の .pth（絶対パス）」「serve.sh の PYTHONPATH」「pytest の
    pythonpath」に分かれ、しかも .pth だけが**インストール時のチェックアウト**を指していた。
    その結果 git worktree から起動しても main の実装が読まれ、殻（新）＋実装（旧）の組合せで
    ``/tf_period_profile`` が 500 になった（2026-08-08 実測）。「worktree で実 UI 検証した」も
    「worktree で pytest が緑」も偽になる。

本テストが固定する不変条件:
    1. 台帳が消費者すべてへ伝播している（.pth 生成・serve.sh・pytest 設定）。
    2. 起動元ツリーが .pth より**優先**される（PYTHONPATH の前置が実際に効く）。
    3. serve.sh が PYTHONPATH を自前で上書きしない（台帳経由の解決を迂回しない）。
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tools.install_dev_paths import path_entries

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "tools" / "dev_paths.txt"

#: 台帳から解決すべき serve.sh 群（起動経路はこれで全部）。
_SERVE_SCRIPTS = [
    _ROOT / "indigators" / "indicator_ui" / "serve.sh",
    _ROOT / "simulator" / "replay_ui" / "serve.sh",
    _ROOT / "unified_ui" / "serve.sh",
]


def _ledger_relative() -> "list[str]":
    out: "list[str]" = []
    for raw in _LEDGER.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def test_ledger_is_relative_and_non_empty():
    """台帳は「チェックアウト相対」で書かれている（絶対パスを書いた時点で worktree が壊れる）。"""
    entries = _ledger_relative()
    assert entries, "tools/dev_paths.txt が空です"
    absolute = [e for e in entries if e.startswith("/")]
    assert not absolute, f"絶対パスは書けません（worktree で main を指す）: {absolute}"


def test_pth_installer_derives_from_the_ledger():
    """.pth 生成器が台帳から導出している（値の書き写しを持たない）。"""
    got = [str(p) for p in path_entries(_ROOT)]
    want = [str(_ROOT if e == "." else _ROOT / e) for e in _ledger_relative()]
    assert got == want

    src = (_ROOT / "tools" / "install_dev_paths.py").read_text(encoding="utf-8")
    assert '"market_profile"' not in src, (
        "install_dev_paths.py にパスの写しがあります（台帳から導出してください）"
    )


def test_pytest_pythonpath_matches_the_ledger():
    """pytest の pythonpath が台帳と一致する（テスト時も起動元ツリーから解決する）。"""
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    got = cfg["tool"]["pytest"]["ini_options"]["pythonpath"]
    assert got == _ledger_relative(), (
        "pyproject.toml の pythonpath が台帳と食い違います（同時に更新してください）"
    )


@pytest.mark.parametrize("script", _SERVE_SCRIPTS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_serve_script_resolves_paths_from_its_own_location(script: Path):
    """serve.sh が台帳ヘルパを source し、PYTHONPATH を自前で上書きしない。"""
    src = script.read_text(encoding="utf-8")
    assert 'tools/dev_paths.sh' in src, (
        f"{script.name} が tools/dev_paths.sh を source していません"
        "（.pth 依存＝worktree で main の実装が読まれます）"
    )
    assert 'PYTHONPATH="$REPO_ROOT"' not in src, (
        f"{script.name} が PYTHONPATH を上書きしています（台帳の解決を捨てています）"
    )


def test_launching_tree_wins_over_the_installed_pth(tmp_path: Path):
    """**実行して固定**: PYTHONPATH に前置したツリーが venv の .pth より優先される。

    これが崩れると、worktree から起動しても main の実装が読まれる（ISSUE-279 の本体）。
    ここでは worktree を模した一時ツリーへスタブ ``market_profile_api`` を置き、
    そちらが解決されることを実測する。
    """
    stub_root = tmp_path / "fake_checkout" / "indigators" / "market_profile" / "api"
    (stub_root / "market_profile_api").mkdir(parents=True)
    (stub_root / "market_profile_api" / "__init__.py").write_text(
        "MARKER = 'stub'\n", encoding="utf-8"
    )

    code = "import market_profile_api as m; print(getattr(m, 'MARKER', 'real'), m.__file__)"
    env_path = f"{tmp_path / 'fake_checkout'}:{stub_root}"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path), env={"PYTHONPATH": env_path, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    marker, resolved = proc.stdout.split(maxsplit=1)
    assert marker == "stub", (
        "PYTHONPATH に前置したツリーが .pth に負けました"
        f"（解決先: {resolved.strip()}）。起動元ツリー優先という前提が崩れています。"
    )


def test_dev_paths_helper_prepends_and_preserves_existing_pythonpath(tmp_path: Path):
    """ヘルパは台帳を前置し、既存 PYTHONPATH を捨てない（呼び出し側の意図を壊さない）。"""
    script = (
        f'REPO_ROOT="{_ROOT}"\n'
        f'. "{_ROOT}/tools/dev_paths.sh"\n'
        'printf "%s" "$PYTHONPATH"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False,
        env={"PYTHONPATH": "/keep/me", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    got = proc.stdout.split(":")
    want = [str(_ROOT if e == "." else _ROOT / e) for e in _ledger_relative()]
    assert got == want + ["/keep/me"], f"PYTHONPATH の組み立てが規約と違います: {got}"
