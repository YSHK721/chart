"""宣言整合性検定の pytest 入口。

このテストは対象コードを import しない。AST のみを読むため、
collection error が残っている状態でも単独で実行できる。

    pytest .claude/scripts/test_declaration_integrity.py
"""

from __future__ import annotations

import json
from pathlib import Path

import declaration_integrity as di
import quality_scope
from declaration_integrity import run, _infer_prefixes

quality_scope.apply(di)

# this file: <repo>/.claude/scripts/ -> parents[2] = リポジトリ根。
REPO = Path(__file__).resolve().parents[2]
# baseline は test_static_quality.py と同一ファイルを見る（2 系統を作らない）。
BASELINE = Path(__file__).with_name("di_baseline.json")
CHECKS = {"C1", "C2", "C3"}


def _frozen() -> set[str]:
    if not BASELINE.exists():
        return set()
    return set(json.loads(BASELINE.read_text(encoding="utf-8")))


def test_no_new_declaration_violations() -> None:
    vs = run(REPO, _infer_prefixes(REPO), CHECKS)
    frozen = _frozen()
    new = [v for v in vs if v.ident() not in frozen]
    assert not new, "新規の宣言整合性違反:\n" + "\n".join(
        f"  {v.check} {v.path}:{v.line} {v.key} — {v.detail}" for v in new
    )


def test_baseline_is_not_stale() -> None:
    """解消済みの違反が baseline に残り続けることを防ぐ（後退の禁止）。"""
    vs = run(REPO, _infer_prefixes(REPO), CHECKS)
    stale = _frozen() - {v.ident() for v in vs}
    assert not stale, (
        f"baseline に解消済みの {len(stale)} 件が残っている。"
        " --write-baseline で更新する:\n  " + "\n  ".join(sorted(stale)[:20])
    )


# ------------------------------------------------ C1: 別名 import（ISSUE-514）
# C1 の定義は「到達可能（import 済み or ローカル定義）」。`from M import X as Y` で
# import した X を `M.X` / `X` と名指すコメントは import 済みであり、違反ではない。
#: 被検査ファイルは pkg の外に置く（自モジュール名の自己言及で pkg が到達可能になるのを避ける）。
_REL = Path("other/user.py")


def _index() -> "di.SymbolIndex":
    idx = di.SymbolIndex()
    idx.add_module(Path("pkg/source_mod.py"))
    idx.add_def("REQUIRED_COLUMNS", Path("pkg/source_mod.py"))
    return idx


def _c1(src: str) -> "list[str]":
    import ast
    return [v.key for v in di.check_declarations(_REL, ast.parse(src), src, _index())]


def test_c1_accepts_a_symbol_imported_under_an_alias() -> None:
    """別名 import した記号を `mod.X` で名指しても到達不能と判定しない。"""
    # Arrange
    src = (
        "from pkg.source_mod import REQUIRED_COLUMNS as _COLS\n"
        "#: 列は `source_mod.REQUIRED_COLUMNS` に従う。\n"
        "X = _COLS\n"
    )

    # Act / Assert
    assert _c1(src) == []


def test_c1_accepts_a_module_imported_under_an_alias() -> None:
    """`from pkg import mod as _m` の mod を `pkg.source_mod` で名指しても違反ではない。"""
    # Arrange
    src = "from pkg import source_mod as _m\n#: `pkg.source_mod` を借りる。\n"

    # Act / Assert
    assert _c1(src) == []


def test_c1_still_flags_a_symbol_that_is_not_imported() -> None:
    """陽性対照: 別名 import が別の記号なら、名指した記号は依然として到達不能。"""
    # Arrange
    src = (
        "from pkg.source_mod import OTHER as _O\n"
        "#: 列は `source_mod.REQUIRED_COLUMNS` に従う。\n"
    )

    # Act / Assert
    assert _c1(src) == ["source_mod.REQUIRED_COLUMNS"]


def test_c1_alias_bindings_are_one_per_aliased_name() -> None:
    """計算量: 別名由来の到達経路は別名 import 1 件につき 1 つだけ作る（2 点で固定）。

    作った経路 − 別名 import の件数 = 0（別名の無い import から経路を作って捨てない）。
    """
    import ast

    def extra_paths(src: str) -> int:
        tree = ast.parse(src)
        _, modules = di.bound_names(tree)
        base = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        return len(modules - base)

    def source(aliased: int, plain: int) -> str:
        lines = [f"from pkg.m{i} import A{i} as _a{i}" for i in range(aliased)]
        lines += [f"from pkg.p{i} import B{i}" for i in range(plain)]
        return "\n".join(lines) + "\n"

    # Act / Assert — 2 点（別名 1・素 5 / 別名 3・素 20）
    assert extra_paths(source(1, 5)) == 1
    assert extra_paths(source(3, 20)) == 3
