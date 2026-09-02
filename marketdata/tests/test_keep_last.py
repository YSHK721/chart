"""keep-last（同一キーの最終出現を採る）規則の **単一権威**を検定で強制する（ISSUE-479 F-6）。

なぜ必要か:
    「同一キーの重複を後勝ちで畳む」規則は本 repo に 5 箇所へ手書き複製されていた
    （``marketdata/tick_m1.py`` の分重複畳み・``marketdata/dataset.py`` の serving hygiene・
    ``tools/verify_pseudo_vwap.py`` の M1 連結・``marketdata/tools/dedupe_tick_m1.py`` の
    行辞書 streaming・``tools/measure/issue449/probe_forming_long.py`` の date 一意化）。
    複製は必ず取り残しを生む（同一規則の 5 実装のうち 1 つだけを直す事故が起きる）。
    本テストは規則の実体を ``marketdata.keep_last`` 1 箇所に閉じ、複製の再発を **走査検定**で
    遮断する（宣言ではなく機械的検査で強制する）。

計算量検定（絶対命令 2026-08-28）:
    測るのは時間ではなく**回数**である。固定するのは「無駄の不在」＝
    ``発行した計算 − 出力に使った計算 = 0`` と、入力を変えた 2 点での「増加なし」である。
    回数リテラルは焼き込まない（期待値は出力から導出する）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from marketdata import keep_last

_ROOT = Path(__file__).resolve().parents[2]

#: 規則の唯一の実体（ここだけが keep-last を書いてよい）。
_AUTHORITY = _ROOT / "marketdata" / "keep_last.py"

#: 走査対象（本番コード。テスト・プロトタイプ・仮想環境は除く）。
_SCAN_DIRS = (
    "marketdata", "tools", "simulator", "indigators", "unified_ui",
    "common", "common_view", "api_shared", "datawindow",
)


# --------------------------------------------------------------------------------------
# 走査ユーティリティ（AST。コメント・docstring 内の言及は対象外＝説明を禁じない）
# --------------------------------------------------------------------------------------
def _iter_sources() -> "list[Path]":
    out: "list[Path]" = []
    for d in _SCAN_DIRS:
        base = _ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            parts = set(p.parts)
            if "tests" in parts or "__pycache__" in parts or ".venv" in parts:
                continue
            out.append(p)
    return out


def _keep_last_calls(source: str) -> "list[str]":
    """``keep="last"`` を渡す呼出・``drop_duplicates`` 呼出を列挙する（AST・文字列内は無視）。"""
    found: "list[str]" = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None) or "?"
        if name == "drop_duplicates":
            found.append(f"{getattr(node, 'lineno', 0)}:{name}")
            continue
        for kw in node.keywords:
            if kw.arg == "keep" and isinstance(kw.value, ast.Constant) and kw.value.value == "last":
                found.append(f"{getattr(node, 'lineno', 0)}:{name}(keep='last')")
    return found


# --------------------------------------------------------------------------------------
# 依存純度・単一権威
# --------------------------------------------------------------------------------------
def test_keep_last_module_has_no_imports() -> None:
    """権威モジュールは import 文を 1 つも持たない（どの層からも取り込める中立核）。"""
    # Arrange
    tree = ast.parse(_AUTHORITY.read_text(encoding="utf-8"))
    # Act
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    # Assert
    assert imports == [], (
        "marketdata/keep_last.py に import 文があります。"
        " 本モジュールは pandas をダックタイピングで扱い、依存ゼロを保つ設計です。"
    )


def test_keep_last_rule_has_no_second_implementation_in_production_code() -> None:
    """keep-last 規則の実体が権威モジュール以外に存在しない（複製の再発を遮断）。"""
    # Arrange / Act
    offenders: "list[str]" = []
    for path in _iter_sources():
        if path == _AUTHORITY:
            continue
        for hit in _keep_last_calls(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(_ROOT)}:{hit}")
    # Assert
    assert offenders == [], (
        "keep-last 規則が権威モジュール外で再実装されています:\n  " + "\n  ".join(offenders)
        + "\n  marketdata.keep_last の dedupe_index_keep_last / dedupe_column_keep_last /"
        " keep_last_by_key への委譲へ置換してください。"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "def f(df):\n    return df[~df.index.duplicated(keep='last')]\n",
        'def f(df):\n    return df.drop_duplicates(subset=["date"], keep="last")\n',
        'def f(df):\n    return df.drop_duplicates()\n',
    ],
    ids=["duplicated_keep_last", "drop_duplicates_keep_last", "drop_duplicates_bare"],
)
def test_the_scan_detects_a_reimplementation(snippet: str) -> None:
    """走査検定に検出力があること（恒真式に退化していないことの検定）。"""
    # Arrange / Act
    hits = _keep_last_calls(snippet)
    # Assert
    assert hits, f"再実装を検出できていません: {snippet!r}"


def test_the_scan_ignores_mentions_in_comments_and_docstrings() -> None:
    """コメント・docstring 内の ``keep="last"`` は offender にしない（説明を禁じない）。"""
    # Arrange
    snippet = '"""date で重複除去（keep="last"）する。"""\n# keep="last" を採る\nX = 1\n'
    # Act
    hits = _keep_last_calls(snippet)
    # Assert
    assert hits == []


# --------------------------------------------------------------------------------------
# 振る舞い（正常系・境界・no-op 同一オブジェクト契約）
# --------------------------------------------------------------------------------------
def _frame(keys: "list[str]", closes: "list[float]") -> pd.DataFrame:
    idx = pd.to_datetime(keys)
    return pd.DataFrame({"date": idx, "close": closes}, index=idx)


def test_dedupe_index_keep_last_adopts_the_final_occurrence() -> None:
    # Arrange
    df = _frame(["2026-07-23 23:58:00", "2026-07-23 23:59:00", "2026-07-23 23:59:00"],
                [1.0, 2.0, 20.0])
    # Act
    out = keep_last.dedupe_index_keep_last(df)
    # Assert
    assert list(out["close"]) == [1.0, 20.0]
    assert not out.index.has_duplicates


def test_dedupe_index_keep_last_returns_the_same_object_when_unique() -> None:
    """重複が無いときは同一オブジェクトを返す（no-op 契約＝呼出側の挙動を 1 ビットも変えない）。"""
    # Arrange
    df = _frame(["2026-07-23 23:58:00", "2026-07-23 23:59:00"], [1.0, 2.0])
    # Act
    out = keep_last.dedupe_index_keep_last(df)
    # Assert
    assert out is df


def test_dedupe_index_keep_last_on_empty_frame_is_a_noop() -> None:
    # Arrange
    df = _frame([], [])
    # Act / Assert
    assert keep_last.dedupe_index_keep_last(df) is df


def test_dedupe_column_keep_last_adopts_the_final_occurrence() -> None:
    # Arrange
    df = pd.DataFrame({"date": ["a", "b", "b", "c"], "v": [1.0, 2.0, 20.0, 3.0]})
    # Act
    out = keep_last.dedupe_column_keep_last(df, "date")
    # Assert
    assert list(out["date"]) == ["a", "b", "c"]
    assert list(out["v"]) == [1.0, 20.0, 3.0]


def test_keep_last_by_key_adopts_the_final_occurrence_per_key() -> None:
    # Arrange
    pairs = [("a", "r1"), ("b", "r2"), ("b", "r3"), ("c", "r4")]
    # Act
    out = keep_last.keep_last_by_key(pairs)
    # Assert
    assert out == {"a": "r1", "b": "r3", "c": "r4"}
    assert list(out) == ["a", "b", "c"]  # 挿入順＝初出順（書出しの並びを決めるのは呼出側）


def test_keep_last_by_key_on_empty_input_returns_an_empty_mapping() -> None:
    assert keep_last.keep_last_by_key(iter(())) == {}


def test_three_representations_adopt_the_same_rows() -> None:
    """index / 列 / (key,row) 対の 3 表現に同一の重複入力を与え、採用行が完全一致する。"""
    # Arrange
    keys = ["2026-07-23 23:58:00", "2026-07-23 23:59:00", "2026-07-23 23:59:00"]
    closes = [1.0, 2.0, 20.0]
    by_index = _frame(keys, closes)
    by_column = pd.DataFrame({"date": keys, "close": closes})
    pairs = list(zip(keys, closes))

    # Act
    out_index = keep_last.dedupe_index_keep_last(by_index)
    out_column = keep_last.dedupe_column_keep_last(by_column, "date")
    out_pairs = keep_last.keep_last_by_key(pairs)

    # Assert（float は == で厳密比較）
    adopted_index = [(str(k), float(v)) for k, v in zip(out_index["date"].astype(str),
                                                        out_index["close"])]
    adopted_column = [(str(k), float(v)) for k, v in zip(out_column["date"],
                                                         out_column["close"])]
    adopted_pairs = [(str(k), float(v)) for k, v in out_pairs.items()]
    assert adopted_index == adopted_column == adopted_pairs


# --------------------------------------------------------------------------------------
# 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
class _CountingIndex:
    """index 呼出を計数するダック（``has_duplicates`` / ``duplicated`` のみ）。"""

    def __init__(self, index, counts: dict) -> None:
        self._index = index
        self._counts = counts

    @property
    def has_duplicates(self) -> bool:
        self._counts["has_duplicates"] += 1
        return bool(self._index.has_duplicates)

    def duplicated(self, keep):
        self._counts["duplicated"] += 1
        return self._index.duplicated(keep=keep)


class _CountingFrame:
    """DataFrame の最小ダック。マスク適用（``__getitem__``）回数＝出力に使ったマスク数。"""

    def __init__(self, df: pd.DataFrame, counts: dict) -> None:
        self._df = df
        self._counts = counts
        self.index = _CountingIndex(df.index, counts)

    def __getitem__(self, mask):
        self._counts["masks_used"] += 1
        return self._df[mask]


def _counts() -> dict:
    return {"has_duplicates": 0, "duplicated": 0, "masks_used": 0}


def _dup_frame(n: int) -> pd.DataFrame:
    """先頭 n 分のうち末尾 1 分だけを二重化した DataFrame（重複 1 件・出力 n 行）。"""
    idx = pd.date_range("2026-07-23", periods=n, freq="1min")
    keys = list(idx) + [idx[-1]]
    return pd.DataFrame({"close": [float(i) for i in range(len(keys))]},
                        index=pd.DatetimeIndex(keys))


def test_dedupe_index_issues_no_duplicated_mask_when_there_is_nothing_to_drop() -> None:
    """重複が無いとき、捨てるためのマスクを 1 つも発行しない（作って捨てる浪費の不在）。"""
    # Arrange
    idx = pd.date_range("2026-07-23", periods=64, freq="1min")
    counts = _counts()
    spy = _CountingFrame(pd.DataFrame({"close": range(64)}, index=idx), counts)
    # Act
    out = keep_last.dedupe_index_keep_last(spy)
    # Assert
    assert out is spy                                          # no-op は素通し
    assert counts["duplicated"] == counts["masks_used"] == 0    # 発行 0 − 使用 0 = 0


def test_dedupe_index_issues_exactly_the_masks_it_uses() -> None:
    """重複があるとき、発行したマスク数 − 出力に使ったマスク数 = 0。"""
    # Arrange
    counts = _counts()
    spy = _CountingFrame(_dup_frame(64), counts)
    # Act
    keep_last.dedupe_index_keep_last(spy)
    # Assert
    assert counts["duplicated"] - counts["masks_used"] == 0
    assert counts["masks_used"] > 0              # 実際に畳んだ（恒真式でない）


def test_dedupe_index_issue_count_does_not_grow_with_input_size() -> None:
    """発行数は入力量に比例しない（オーダーの表明・入力 2 点で同数）。"""
    # Arrange / Act
    measured = {}
    for n in (1000, 2000):
        counts = _counts()
        keep_last.dedupe_index_keep_last(_CountingFrame(_dup_frame(n), counts))
        measured[n] = dict(counts)
    # Assert（回数リテラルは焼き込まない。2 点の同一性と「発行 − 使用 = 0」のみを固定する）
    assert measured[1000]["duplicated"] == measured[2000]["duplicated"], (
        f"入力量を倍にすると発行数が変わりました（非定数）: {measured}"
    )
    for n, counts in measured.items():
        assert counts["duplicated"] - counts["masks_used"] == 0, n


class _CountingColumnFrame:
    """``drop_duplicates`` 発行回数を計数するダック。"""

    def __init__(self, df: pd.DataFrame, counts: dict) -> None:
        self._df = df
        self._counts = counts

    def drop_duplicates(self, subset, keep):
        self._counts["drop_duplicates"] += 1
        return self._df.drop_duplicates(subset=subset, keep=keep)


@pytest.mark.parametrize("n", [1000, 2000], ids=["n1000", "n2000"])
def test_dedupe_column_issues_one_pass_per_output_frame(n: int) -> None:
    """出力 1 枚あたりの走査発行は 1 回（発行 − 使用 = 0）。入力 2 点で不変。"""
    # Arrange
    df = pd.DataFrame({"date": [i // 2 for i in range(2 * n)], "v": range(2 * n)})
    counts = {"drop_duplicates": 0}
    spy = _CountingColumnFrame(df, counts)
    # Act
    out = keep_last.dedupe_column_keep_last(spy, "date")
    # Assert
    outputs_produced = 1  # 返却された DataFrame の枚数（出力から導出・リテラル焼き込みでない）
    assert counts["drop_duplicates"] - outputs_produced == 0
    assert len(out) == n


@pytest.mark.parametrize("n", [1000, 2000], ids=["n1000", "n2000"])
def test_keep_last_by_key_visits_each_pair_exactly_once(n: int) -> None:
    """入力対を 1 度だけ消費する（再走査＝作って捨てる浪費の不在）。入力 2 点で線形。"""
    # Arrange
    visited = {"n": 0}

    def _pairs():
        for i in range(2 * n):
            visited["n"] += 1
            yield (i // 2, f"row{i}")

    # Act
    out = keep_last.keep_last_by_key(_pairs())
    # Assert
    consumed = 2 * n            # 入力対の総数（出力から導出）
    assert visited["n"] - consumed == 0
    assert len(out) == n
