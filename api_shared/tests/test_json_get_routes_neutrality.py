"""GET の JSON ルート骨格を中立共有パッケージ api_shared が所有すること（ISSUE-479 Wave2b）。

なぜ移設するか（実測した構造の歪み）:
    `GetRouteResponder` は sim_ui/framework に置かれていたが、replay_ui/framework の 4 つの
    App（candles / catalog / intraday / profiles）がそれを import していた。これは
    **replay_ui → sim_ui** の横断依存であり、同格であるべき 2 つの配信殻の間に上下を作る。
    実測（AST 走査）では replay_ui の本番コードから sim_ui への import は 4 件、その**全件**が
    `GetRouteResponder` 1 個の借用だった。つまりこの 1 個を中立へ移せば辺が消える。

なぜ api_shared か:
    「prefix で JSON ルートを選び、外れたら静的配信へ落とす」骨格は、どちらの殻のアクターにも
    属さない純粋物である。http_contract（error.type→ステータス表）と同格の共有物として
    中立パッケージが所有する。

依存純度（本検定が固定する不変条件）:
    api_shared は **stdlib のみ**に依存する。移設元 `json_get_routes.py` は
    simulator.sim_ui.adapter.job_api_controller.ApiResponse を import していたが、これは
    `write_json` の型注釈のためだけの import であり、`from __future__ import annotations` の
    下で実行時には評価されない。しかも replay 側のルート関数は ApiResponse ではなく
    ``(status, payload)`` タプルを返しており、この注釈は**既に実体と食い違っていた**。
    したがって移設にあたり注釈を構造的プロトコル（`typing.Protocol`＝stdlib）へ置き換える。
    これは注釈の是正であって、実行時の挙動は 1 バイトも変わらない。

    純度を宣言でなく検査で固定する（`test_replay_purity.py` と同じ 2 段構え）:
      1. **構造**: AST 走査で import 根が stdlib に収まること。
      2. **実行**: 新しいインタプリタで import し `sys.modules` に配信殻が現れないこと。
         AST だけでは推移的な流入を検出できない。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_API_SHARED = Path(__file__).resolve().parents[1]
_REPO_ROOT = _API_SHARED.parent

#: api_shared 配下の本番コード（tests を除く）。
_PRODUCTION_FILES = sorted(
    p
    for p in _API_SHARED.rglob("*.py")
    if "tests" not in p.relative_to(_API_SHARED).parts
)

#: 中立パッケージが依存してよい根＝stdlib と自分自身のみ。
_ALLOWED_ROOTS = set(sys.stdlib_module_names) | {"api_shared"}


def _imported_roots(path: Path) -> "set[str]":
    """ファイルが import する最上位パッケージ名の集合（相対 import は自パッケージ扱い）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.add("api_shared")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


# --------------------------------------------------------------------------------------
# 1. 所有者の実在と公開面
# --------------------------------------------------------------------------------------
def test_the_neutral_package_owns_the_json_get_route_skeleton() -> None:
    """骨格の実体が api_shared にあること（借用元が中立へ移っている）。"""
    import api_shared.json_get_routes as owner

    assert callable(owner.write_json)
    assert isinstance(owner.GetRouteResponder, type)


def test_the_skeleton_is_not_left_behind_in_the_serving_shell() -> None:
    """旧所在に再エクスポートを残さないこと（実体が 2 箇所に見える状態を作らない）。

    再エクスポートを残すと「どちらを import してもよい」状態になり、依存の辺が
    静かに復活する。旧所在は**存在しない**ことを固定する。
    """
    legacy = _REPO_ROOT / "simulator" / "sim_ui" / "framework" / "json_get_routes.py"
    assert not legacy.exists(), (
        "旧所在 simulator/sim_ui/framework/json_get_routes.py が残っています。"
        " 移設は再エクスポートを残さず、消費者の import を全数付け替える形で行います。"
    )


# --------------------------------------------------------------------------------------
# 2. 依存純度（構造）
# --------------------------------------------------------------------------------------
def test_the_scan_reaches_the_production_modules() -> None:
    """走査の取りこぼしで純度検定が空振りしないこと（ゲートの自己検査）。"""
    scanned = {str(p.relative_to(_API_SHARED)) for p in _PRODUCTION_FILES}
    assert "http_contract.py" in scanned
    assert "json_get_routes.py" in scanned


@pytest.mark.parametrize("path", _PRODUCTION_FILES, ids=lambda p: p.name)
def test_production_modules_import_only_stdlib(path: Path) -> None:
    """中立パッケージは stdlib のみに依存する（配信殻・外部技術を知らない）。"""
    foreign = sorted(_imported_roots(path) - _ALLOWED_ROOTS)
    assert not foreign, (
        f"{path.name} が stdlib 以外を import しています: {foreign}。"
        " api_shared は配信殻に属さない純粋物のみを置く場所です。"
    )


def test_the_purity_detector_flags_a_serving_shell_import(tmp_path: Path) -> None:
    """検出力の自己検査: 配信殻の import を見逃さないこと。

    これが無いと `_ALLOWED_ROOTS` が広すぎた場合に検定が恒真化する。
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from simulator.sim_ui.adapter.job_api_controller import ApiResponse\n",
        encoding="utf-8",
    )
    assert sorted(_imported_roots(sample) - _ALLOWED_ROOTS) == ["simulator"]


def test_the_purity_detector_accepts_stdlib_only(tmp_path: Path) -> None:
    """誤検出しないこと（stdlib と相対 import は違反ではない）。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "from typing import Any, Protocol\n"
        "from .http_contract import nested_error\n",
        encoding="utf-8",
    )
    assert _imported_roots(sample) - _ALLOWED_ROOTS == set()


# --------------------------------------------------------------------------------------
# 3. 依存純度（実行）— AST では見えない推移的流入を実測する
# --------------------------------------------------------------------------------------
def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts)


_RUNTIME_MODULES = [_module_name(p) for p in _PRODUCTION_FILES]


@pytest.mark.parametrize("module", _RUNTIME_MODULES, ids=lambda m: m.rsplit(".", 1)[-1])
def test_importing_a_neutral_module_does_not_load_a_serving_shell(module: str) -> None:
    """**実行**して固定する: import しただけでは配信殻がロードされない。"""
    code = (
        "import sys;"
        f"import {module};"
        "leaked=sorted({'simulator','indigators','marketdata'} & set(sys.modules));"
        "print(','.join(leaked))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"import に失敗: {proc.stderr.strip()[-500:]}"
    leaked = proc.stdout.strip()
    assert not leaked, (
        f"{module} を import しただけで {leaked} がロードされます（推移的な流入）。"
        " 中立パッケージの宣言が偽になっています。"
    )
