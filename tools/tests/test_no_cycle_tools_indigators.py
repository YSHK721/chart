"""アーキ回帰: ``indigators.indicator_ui.tools`` → ``tools`` の循環依存を禁ずる（ISSUE-479 F-3）。

``tools`` パッケージは横断的な運用スクリプトのアクターであり、``indigators.indicator_ui`` は
チャート UI のアクターである。両者は別アクターなので依存辺を持ってはならない。ところが
``export_jp225_m1.py`` が汎用ポーリングループを ``tools.watch_loop`` から import しており、
``tools`` ↔ ``indigators`` の相互参照（循環 C-2）が成立していた。

規則の実体（``run_watch``）は stdlib のみで書かれた**中立核**であり、どちらのアクターにも属さない。
実体は ``common.watch_loop`` へ移し、両アクターはそこを参照する。本テストはその向きを固定する
（AST 走査＝構造で固定し、実行時の import 有無に依存しない）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UI_TOOLS = _REPO_ROOT / "indigators" / "indicator_ui" / "tools"

#: 走査対象（本番のみ・テストと ``__init__`` は除外）。
_SOURCES = sorted(
    p
    for p in _UI_TOOLS.rglob("*.py")
    if "tests" not in p.relative_to(_UI_TOOLS).parts and p.name != "__init__.py"
)

#: 禁じるパッケージ根（別アクターへの依存辺）。
_FORBIDDEN_ROOT = "tools"


def _imported_roots(source: str) -> set[str]:
    """ソース文字列の import 文から、パッケージ根の集合を返す（絶対 import のみ）。"""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def test_scan_target_is_not_empty() -> None:
    """走査対象が空なら本テストは恒真式に退化する（検査の生存確認）。"""
    assert _SOURCES, f"走査対象が空です: {_UI_TOOLS}"


@pytest.mark.parametrize("path", _SOURCES, ids=lambda p: p.name)
def test_indicator_ui_tools_do_not_import_the_tools_actor(path: Path) -> None:
    """``indigators/indicator_ui/tools`` の本番モジュールは ``tools`` パッケージを import しない。

    識別力: ``from tools.watch_loop import run_watch`` を戻すと Red になる。中立核は
    ``common.watch_loop`` を参照すること。
    """
    roots = _imported_roots(path.read_text(encoding="utf-8"))
    assert _FORBIDDEN_ROOT not in roots, (
        f"{path.relative_to(_REPO_ROOT)} が別アクター '{_FORBIDDEN_ROOT}' を import しています"
        "（循環 C-2）。中立核は common へ置き、双方がそこを参照すること。"
    )


def test_detects_a_synthetic_offender() -> None:
    """検出力: 合成ソース（実ファイルを作らない）で違反 3 形態を検出し、非違反を誤検出しない。"""
    assert _FORBIDDEN_ROOT in _imported_roots("from tools.watch_loop import run_watch\n")
    assert _FORBIDDEN_ROOT in _imported_roots("import tools.watch_loop\n")
    assert _FORBIDDEN_ROOT in _imported_roots("    from tools import watch_loop\n".strip())
    # 非違反: 中立核・自パッケージ・stdlib は素通しする。
    assert _FORBIDDEN_ROOT not in _imported_roots("from common.watch_loop import run_watch\n")
    assert _FORBIDDEN_ROOT not in _imported_roots("from indigators.indicator_ui import x\n")
    assert _FORBIDDEN_ROOT not in _imported_roots("import logging\n")


def test_scan_parses_each_source_exactly_once() -> None:
    """計算量テスト: 走査は 1 ファイル 1 パース（発行 − 判定に使ったソース数 = 0）。

    かつオーダー表明として、対象を 1 件 / 2 件に変えても発行数は対象数だけで決まる
    （ファイル内容の長さ・import 数では増えない）。回数リテラルは焼き込まない。
    """
    parsed: list[str] = []
    real_parse = ast.parse

    def _spy(source, *args, **kwargs):
        parsed.append(source)
        return real_parse(source, *args, **kwargs)

    ast.parse = _spy
    try:
        one = _SOURCES[:1]
        used_one = [_imported_roots(p.read_text(encoding="utf-8")) for p in one]
        issued_one = len(parsed)
        parsed.clear()

        two = _SOURCES[:2] if len(_SOURCES) >= 2 else _SOURCES
        used_two = [_imported_roots(p.read_text(encoding="utf-8")) for p in two]
        issued_two = len(parsed)
    finally:
        ast.parse = real_parse

    assert issued_one - len(used_one) == 0, "1 ファイルあたりのパース発行が判定使用数を超えています"
    assert issued_two - len(used_two) == 0, "1 ファイルあたりのパース発行が判定使用数を超えています"
    assert issued_two == len(two), "パース発行が対象ファイル数以外の要因で増えています"
