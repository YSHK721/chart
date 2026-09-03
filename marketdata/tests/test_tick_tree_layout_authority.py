"""tick tree レイアウトの単一権威を **検定で強制する**（ISSUE-262）。

``marketdata.tick_m1.day_parquet_path`` は自身の docstring で
「tick tree レイアウト ``<DATA_DIR>/ticks/YYYY/MM/DD/<symbol>_ticks.parquet`` の単一権威
（レイアウト変更を本所 1 箇所に閉じる）」と宣言している。しかし実際には
``tools/acquire_marketdata.py`` / ``tools/build_tick_rollup.py`` が ``/ticks`` を、
``simulator/tools/fetch_ticks_ymd.py`` が ``YYYY/MM/DD`` とファイル名を独自に組んでいた。
宣言は施行されていなかった。

本テストはリポジトリを AST/文字列で走査し、権威モジュール以外がレイアウトを組んでいないことを
固定する。レイアウトを変えるときに触るべき箇所が 1 つであることを、宣言ではなく検定で保証する。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: レイアウトの権威（ここだけが tick tree のパスを組んでよい）。
#: ISSUE-479 M-2 で権威を ``tick_m1.py`` から専用モジュールへ移した。除外対象が 1 ファイル
#: 狭まる＝``tick_m1.py`` も走査対象に入るため、本変更は許容集合の**縮小**（検定の強化）である。
_AUTHORITY = _ROOT / "marketdata" / "tick_tree.py"

#: 走査対象（本番コード。テストとプロトタイプ・仮想環境は除く）。
_SCAN_DIRS = ("marketdata", "tools", "simulator", "indigators", "unified_ui")

#: レイアウトの断片を組んでいると判定する式。
_TICK_ROOT = re.compile(r"""/\s*["']ticks["']""")
_YMD_TREE = re.compile(r"""%Y["']\s*/\s*f?["']\{?\w*:?%m""")
_TICK_FILENAME = re.compile(r"""["'][A-Za-z0-9_]+_ticks\.(parquet|empty)["']""")


def _iter_sources() -> "list[Path]":
    out: "list[Path]" = []
    for d in _SCAN_DIRS:
        for p in (_ROOT / d).rglob("*.py"):
            parts = set(p.parts)
            if "tests" in parts or "__pycache__" in parts or ".venv" in parts:
                continue
            out.append(p)
    return out


@pytest.mark.parametrize(
    "pattern,what",
    [(_TICK_ROOT, "tick tree の基点 </ticks>"),
     (_YMD_TREE, "YYYY/MM/DD の階層"),
     (_TICK_FILENAME, "<symbol>_ticks.parquet / .empty のファイル名")],
    ids=["tick_root", "ymd_tree", "filename"],
)
def test_only_the_authority_builds_the_tick_tree_layout(pattern, what):
    """権威モジュール以外が tick tree のレイアウトを組んでいない。

    落ちた場合の直し方: 該当箇所を ``marketdata.tick_m1`` の
    ``tick_root`` / ``day_parquet_path`` / ``day_parquet_files`` への委譲へ置換する。
    レイアウトそのものを変えたいなら、権威モジュール 1 箇所だけを変える。
    """
    offenders: "list[str]" = []
    for path in _iter_sources():
        if path == _AUTHORITY:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue          # コメント・docstring の言及は対象外（説明を禁じない）
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_ROOT)}:{i}: {stripped[:90]}")
    assert not offenders, (
        f"{what} を権威モジュール外で組んでいます:\n  " + "\n  ".join(offenders)
        + "\n  marketdata.tick_m1 への委譲へ置換してください。"
    )
