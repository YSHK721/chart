"""「`numpy.datetime64` か」の判定を domain の 1 か所だけに置くゲート（ISSUE-553 の手書き複製）。

何が問題か（事後レビュー 2026-09-29）: `simulator/usecase/bar_times.py` の
``type(t).__name__ == "datetime64"`` は、`simulator/domain/bar_time.py` の判定の写しだった。
判定を 1 か所直した日に写しが取り残され、一括の経路と 1 本ずつの経路で受理する表現が食い違う。

本モジュールが固定する契約（AST 走査）:
  1. 本番コードで型名を文字列 "datetime64" と比べる式は `domain/bar_time.py` にだけ在る。
  2. 許可したモジュールが判定を持ち続けている（改名・移動で空振りしない）。
  3. ゲート自身の検出力: 写しの形を注入したソースを検出し、無関係な比較を検出しない。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: 型名の比較を書いてよい単一ソース（domain は numpy を import しないため duck typing で判定する）。
_OWNER = "domain/bar_time.py"

_TYPE_NAME = "datetime64"


def _production_files() -> "list[Path]":
    return sorted(
        p
        for p in _SIMULATOR_DIR.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )


def _type_name_comparisons(source: str) -> "list[int]":
    """``<...>.__name__`` と文字列 "datetime64" を比べる式の行番号。"""
    lines: "list[int]" = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        has_name = any(isinstance(o, ast.Attribute) and o.attr == "__name__" for o in operands)
        has_literal = any(isinstance(o, ast.Constant) and o.value == _TYPE_NAME for o in operands)
        if has_name and has_literal:
            lines.append(node.lineno)
    return lines


def test_the_type_name_check_exists_only_in_the_domain_owner() -> None:
    violations = [
        f"{rel}:{line}"
        for path in _production_files()
        for rel in [path.relative_to(_SIMULATOR_DIR).as_posix()]
        if rel != _OWNER
        for line in _type_name_comparisons(path.read_text(encoding="utf-8"))
    ]
    assert violations == [], (
        "numpy.datetime64 の判定の写しが domain の外に現れました。"
        f"simulator.domain.bar_time.is_numpy_datetime64 を呼んでください: {violations}"
    )


def test_the_owner_still_holds_the_check() -> None:
    assert _type_name_comparisons((_SIMULATOR_DIR / _OWNER).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "snippet",
    [
        'ok = type(t).__name__ == "datetime64"\n',
        'ok = all(type(t).__name__ == "datetime64" for t in times)\n',
        'ok = "datetime64" == t.__class__.__name__\n',
    ],
)
def test_copies_are_detected(snippet) -> None:
    assert _type_name_comparisons(snippet) == [1]


@pytest.mark.parametrize(
    "snippet",
    ['ok = type(t).__name__ == "Timestamp"\n', 'ok = unit == "datetime64"\n'],
)
def test_unrelated_comparisons_are_not_flagged(snippet) -> None:
    assert _type_name_comparisons(snippet) == []
