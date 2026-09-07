"""供給 tail 上限（50,000 行）の単一定義を **検定で強制する**（ISSUE-502 D-10）。

台帳（.doc/solid_audit_20260906.md の D-10）が実測した状態: 同じ上限が
``marketdata/rollup_store.py`` と ``marketdata/dataset.py`` に各自の数値として置かれ、
「同方式・同値」というコメントだけが人手同期を担っていた（値がずれても何も落ちない）。
さらに ``marketdata/tickvol_profile.py`` の出典コメントは実在しない定義
（``serving_cache.py`` の 50,000）を指していた。

本ファイルは 2 つを固定する。

1. **特性化（byte 等価）**: 上限値が 50,000 のまま、両利用点が同じ値を返す。
2. **単一定義の強制**: 唯一の所有者 :data:`marketdata.tail_reader.SERVING_TAIL_ROWS` 以外に、
   同じ値のリテラル定義が marketdata の本番コードに現れない（AST 走査）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from marketdata import dataset, rollup_store, tail_reader

_PKG = Path(__file__).resolve().parents[1]

#: 値の唯一の所有者（ここだけが数値リテラルを持ってよい）。
_AUTHORITY = _PKG / "tail_reader.py"


# =====================================================================
# 1. 特性化（是正前の実測値と同一）
# =====================================================================

def test_serving_tail_rows_value_is_unchanged() -> None:
    assert tail_reader.SERVING_TAIL_ROWS == 50_000


def test_both_readers_use_the_single_definition() -> None:
    """1m 原子（dataset）と上位足ロールアップ（rollup_store）が同じ値を使う。"""
    assert dataset._ATOMIC_TAIL_LOOKBACK_ROWS == tail_reader.SERVING_TAIL_ROWS
    assert rollup_store._ROLLUP_TAIL_ROWS == tail_reader.SERVING_TAIL_ROWS


# =====================================================================
# 2. 単一定義の強制（AST 走査）
# =====================================================================

def _iter_sources() -> "list[Path]":
    """marketdata 配下の本番コード（テスト・キャッシュを除く）。"""
    return sorted(
        p for p in _PKG.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


def _literal_definitions_over(files, value: int, read=None) -> "list[str]":
    """``value`` を数値リテラルで代入している箇所を列挙する（1 ファイルにつき読込 1 回）。

    コメント・docstring 内の言及は AST に現れないため自然に対象外（説明を禁じない）。
    """
    reader = read or _read_source
    hits: "list[str]" = []
    for path in files:
        tree = ast.parse(reader(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                rhs = node.value
                if (isinstance(rhs, ast.Constant) and isinstance(rhs.value, int)
                        and not isinstance(rhs.value, bool) and rhs.value == value):
                    hits.append(f"{path.name}:{node.lineno}")
    return hits


def _literal_definitions(value: int, read=None) -> "list[str]":
    return _literal_definitions_over(
        [p for p in _iter_sources() if p != _AUTHORITY], value, read=read
    )


def test_only_the_authority_defines_the_serving_tail_rows_value() -> None:
    """供給 tail 上限の第 2 定義が marketdata 本番コードに存在しない。

    落ちた場合の直し方: 該当箇所を ``tail_reader.SERVING_TAIL_ROWS`` の参照へ置換する。
    上限そのものを変えたいなら、権威 1 箇所だけを変える。
    """
    offenders = _literal_definitions(tail_reader.SERVING_TAIL_ROWS)
    assert not offenders, (
        "供給 tail 上限の第 2 定義があります: " + ", ".join(offenders)
        + "。marketdata.tail_reader.SERVING_TAIL_ROWS の参照へ置換してください。"
    )


def test_the_authority_itself_holds_the_literal() -> None:
    """権威側には数値リテラルが存在する（走査が恒真式に退化していないことの片側）。"""
    assert _literal_definitions_over([_AUTHORITY], tail_reader.SERVING_TAIL_ROWS)


@pytest.mark.parametrize(
    "source",
    ["X = 50_000\n", "X = 50000\n", "X: int = 50_000\n", "def f():\n    y = 50000\n"],
    ids=["underscore", "plain", "annotated", "nested"],
)
def test_the_literal_scan_has_detection_power(tmp_path, source) -> None:
    """走査が恒真式に退化していないこと（合成ソースで検出できる）。"""
    p = tmp_path / "probe.py"
    p.write_text(source, encoding="utf-8")
    assert _literal_definitions_over([p], 50_000)


def test_the_literal_scan_ignores_comments_and_references(tmp_path) -> None:
    """コメントの言及・権威への参照は offender にしない。"""
    p = tmp_path / "probe.py"
    p.write_text(
        "# 供給 tail は 50,000 行（marketdata.tail_reader.SERVING_TAIL_ROWS）\n"
        "X = tail_reader.SERVING_TAIL_ROWS\n",
        encoding="utf-8",
    )
    assert _literal_definitions_over([p], 50_000) == []


# ---------------------------------------------------------------------
# 計算量検定（Test Spy・発行 − 使用 = 0）
# ---------------------------------------------------------------------

def test_every_source_is_read_exactly_once_by_the_literal_scan() -> None:
    """読込集合 == 走査対象。読み捨ても二度読みも無い（発行 − 使用 = 0）。"""
    reads: "list[Path]" = []
    _literal_definitions(
        tail_reader.SERVING_TAIL_ROWS,
        read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1],
    )
    used = [p for p in _iter_sources() if p != _AUTHORITY]
    assert len(reads) - len(used) == 0
    assert set(reads) == set(used)
    assert len(set(reads)) - len(reads) == 0


def test_the_read_count_is_determined_by_the_file_count_alone(tmp_path) -> None:
    """走査対象 4 件 / 8 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。

    ファイルの行数を変えても読込は増えない（1 ファイル 1 読込）。
    """
    measured: "dict[int, int]" = {}
    for count in (4, 8):
        files = []
        for i in range(count):
            p = tmp_path / f"m{count}_{i}.py"
            p.write_text("x = 1\n" * (i + 1), encoding="utf-8")
            files.append(p)
        reads: "list[Path]" = []
        _literal_definitions_over(
            files, 50_000,
            read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1],
        )
        measured[count] = len(reads)
    assert measured == {4: 4, 8: 8}
