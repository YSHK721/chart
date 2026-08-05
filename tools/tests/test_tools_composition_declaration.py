"""``tools`` パッケージの「ロジックの重複を持たない合成点」宣言を **検定で強制する**（ISSUE-262）。

``tools/__init__.py`` は「各サブモジュールは既存ライブラリ・ツールの合成点として振る舞い、
ロジックの重複を持たない」と宣言している。しかし実際には
  - ``_rollup_timeframes`` が 2 本（一方の docstring が「他方と同規則」と人手同期を宣言）
  - tick tree レイアウト（``/ticks``・``YYYY/MM/DD``・ファイル名）が独自実装
  - 生 tick 列定義が独自実装
が存在し、宣言は施行されていなかった。

本テストは「同じ規則の第 2 定義が tools 配下に無い」ことを、規則ごとに固定する。
規則を tools に置きたくなったら、それは合成点ではなくライブラリの仕事である
（marketdata / simulator へ置き、tools は呼ぶだけにする）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[1]


def _sources() -> "list[Path]":
    return [p for p in _TOOLS.glob("*.py") if p.name != "__init__.py"]


def _function_bodies(path: Path) -> "dict[str, str]":
    """トップレベル関数名 → 本体ソース（docstring を除く）。"""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()
    out: "dict[str, str]" = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = [n for n in node.body if not (isinstance(n, ast.Expr)
                                                 and isinstance(n.value, ast.Constant))]
            if not body:
                continue
            src = "\n".join(lines[body[0].lineno - 1: node.end_lineno])
            out[node.name] = src
    return out


def test_rollup_timeframe_rule_is_not_reimplemented_in_tools():
    """ロールアップ対象 tf の規則が tools に第 2 定義として存在しない。

    唯一源は ``marketdata.rollup.rollup_timeframes``。tools 側は委譲だけを持つ。
    """
    offenders = []
    for path in _sources():
        for name, src in _function_bodies(path).items():
            if "TIMEFRAME_RULES" in src and "!=" in src and '"1m"' in src:
                offenders.append(f"{path.name}:{name}")
    assert not offenders, (
        f"ロールアップ対象 tf の規則を tools が再実装しています: {offenders}。"
        " marketdata.rollup.rollup_timeframes への委譲へ置換してください。"
    )


def test_raw_tick_columns_are_not_redefined_in_tools():
    """生ティックの正準列が tools に第 2 定義として存在しない。

    唯一源は ``simulator.tools.ingest_ticks.RAW_COLUMNS``。
    """
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            code = line.strip()
            if code.startswith("#"):
                continue
            if "bidPrice" in code and "askPrice" in code and ("=" in code and "[" in code or "(" in code):
                if "RAW_COLUMNS" not in code:
                    offenders.append(f"{path.name}:{i}: {code[:80]}")
    assert not offenders, (
        f"生ティック列を tools が再定義しています:\n  " + "\n  ".join(offenders)
        + "\n  simulator.tools.ingest_ticks.RAW_COLUMNS を import してください。"
    )


def test_tools_declaration_matches_this_test_suite():
    """``tools/__init__.py`` の宣言が、施行されている内容を指している。

    宣言だけを残して施行を持たない状態（今回の再発源）を作らないための固定点。
    """
    text = (_TOOLS / "__init__.py").read_text(encoding="utf-8")
    assert "ロジックの重複を持たない" in text, "宣言文が変わりました。本テストも更新してください。"
    assert "test_tools_composition_declaration" in text, (
        "tools/__init__.py の宣言が、それを強制するテストを指していません。"
        " 宣言と施行を結び付けてください（宣言だけを残さない）。"
    )
