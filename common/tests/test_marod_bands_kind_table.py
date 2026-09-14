"""`common.marod_bands` の因果統計 kind 分岐が **単一表**に閉じることを固定する（ISSUE-479 Wave2 C-4）。

背景（実測）: `stat_reducer`（逐次 reducer の dict）と `rolling_causal_fast`（ベクトル化の
if 連鎖）が同じ kind 集合を **2 箇所**で列挙していた。片方だけに kind を足すと、逐次側の
KeyError が先に出る／ベクトル化側の else へ黙って落ちる、という取り残しが生じる。

本ファイルが固定するもの:
    1. 分岐の定義（dict キー・比較の被演算子）としての kind リテラルが単一表に閉じること（構造）
    2. 表引きの発行回数が「出力に必要な解決回数」と一致し、入力長・窓長で増えないこと（計算量）
    3. 分割前の出力の bit 等価（digest 凍結）

なお quantile_bands / sigma_band が `rolling_causal_fast` へ渡す実引数リテラルは
「呼び出し側による選択」であって分岐の定義ではないため、1. の対象外。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib

import numpy as np
import pytest

from common import marod_bands

_SOURCE_PATH = pathlib.Path(marod_bands.__file__)
_KIND_LITERALS = frozenset({"quantile", "mean", "std"})
_TABLE_NAME = "_KIND_AGGREGATORS"


def _module_tree() -> ast.Module:
    return ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))


def _table_node_ids(tree: ast.Module) -> set[int]:
    """単一表 `_KIND_AGGREGATORS` の定義に属する AST ノード id の集合。"""
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == _TABLE_NAME for t in targets):
            return {id(sub) for sub in ast.walk(node)}
    return set()


def _dispatch_literal_lines(tree: ast.Module, excluded: set[int]) -> list[int]:
    """kind リテラルが「分岐の定義」として現れる行番号（表の定義内は除外）。"""
    lines: list[int] = []
    for node in ast.walk(tree):
        if id(node) in excluded:
            continue
        candidates: list[ast.expr] = []
        if isinstance(node, ast.Dict):
            candidates = [k for k in node.keys if k is not None]
        elif isinstance(node, ast.Compare):
            candidates = [node.left, *node.comparators]
        for expr in candidates:
            if id(expr) in excluded:
                continue
            if isinstance(expr, ast.Constant) and expr.value in _KIND_LITERALS:
                lines.append(expr.lineno)
    return sorted(lines)


def test_kind_dispatch_is_confined_to_a_single_table() -> None:
    # Arrange
    tree = _module_tree()
    excluded = _table_node_ids(tree)

    # Act
    offenders = _dispatch_literal_lines(tree, excluded)

    # Assert: 表の外に kind 分岐の定義が 1 件も無い。
    assert offenders == [], f"kind 分岐が単一表の外に残存: {_SOURCE_PATH.name}:{offenders}"
    assert excluded, f"{_TABLE_NAME} がモジュール直下に定義されていない"


def test_stat_reducer_resolves_through_the_single_table() -> None:
    # Arrange / Act / Assert: 署名・戻り値・未知 kind の KeyError を不変に保つ。
    finite = np.array([1.0, 2.0, 3.0, 4.0])
    assert marod_bands.stat_reducer("mean")(finite) == pytest.approx(2.5)
    assert marod_bands.stat_reducer("std")(finite) == pytest.approx(finite.std(ddof=1))
    assert marod_bands.stat_reducer("quantile", 0.5)(finite) == pytest.approx(2.5)
    with pytest.raises(KeyError):
        marod_bands.stat_reducer("median")


def test_rolling_causal_fast_matches_the_frozen_digest() -> None:
    """分割前の実装で凍結した digest と bit 一致する（NaN 混在ランダム系列・全 kind・4 窓）。"""
    # Arrange: 欠損を先頭・中間・末尾に置き、部分窓/満杯窓の両区間を踏む。
    rng = np.random.default_rng(20260903)
    values = rng.normal(0.0, 1.0, 600)
    values[:11] = np.nan
    values[200:207] = np.nan
    values[599] = np.nan

    # Act
    digest = hashlib.sha256()
    for window_n in (3, 10, 50, 700):
        for kind, q in (("quantile", 0.05), ("quantile", 0.95), ("mean", None), ("std", None)):
            digest.update(marod_bands.rolling_causal_fast(values, window_n, kind, q).tobytes())

    # Assert
    assert digest.hexdigest() == (
        "d5f2130e58b8b7011ca5ce29b133a62b8e33e2a9203e32b44d3fe93cc29abb5e"
    )


def test_rolling_causal_fast_matches_the_loop_reference_with_nan() -> None:
    """ベクトル化と逐次ループが全バー一致（NaN 位置含む）— 独立オラクルによる同一性。

    許容差は既存の恒久固定（indigators/btlm_trail_marod/tests/test_marod.py の
    test_rolling_causal_fast_matches_loop_reference）と同一の 1e-12。ベクトル化側は
    `np.nanquantile` 系、逐次側は `np.quantile` 系で、集約順序の差により最終桁
    （実測 5.6e-17）がずれるため、bit 等価は digest 凍結テストの側で固定する。
    """
    rng = np.random.default_rng(11)
    values = rng.normal(0.0, 1.0, 300)
    values[:9] = np.nan
    values[100:104] = np.nan

    for window_n in (3, 25, 400):
        for kind, q, reducer in (
            ("quantile", 0.05, lambda f: np.quantile(f, 0.05)),
            ("mean", None, lambda f: f.mean()),
            ("std", None, lambda f: f.std(ddof=1)),
        ):
            fast = marod_bands.rolling_causal_fast(values, window_n, kind, q)
            slow = marod_bands.rolling_causal(values, window_n, reducer)
            np.testing.assert_allclose(fast, slow, rtol=1e-12, atol=1e-12, equal_nan=True)


class _CountingTable(dict):
    """Test Spy: 表引き（`__getitem__`）の発行回数を数える。"""

    def __init__(self, base: dict) -> None:
        super().__init__(base)
        self.lookups = 0

    def __getitem__(self, key):  # noqa: D105
        self.lookups += 1
        return super().__getitem__(key)


@pytest.mark.parametrize(("n", "window_n"), [(50, 10), (500, 50)])
@pytest.mark.parametrize("calls", [1, 3])
def test_kind_table_lookups_equal_the_resolutions_the_output_needs(
    monkeypatch: pytest.MonkeyPatch, n: int, window_n: int, calls: int
) -> None:
    """計算量テスト: 発行した表引き − 出力に使った kind 解決 = 0。

    「使った解決」は `rolling_causal_fast` の呼び出し 1 回につき 1 件（1 呼び出しは
    1 つの kind しか必要としない）。回数そのものを焼き込まず、**無駄の不在**を固定する。
    入力長 n・窓長 window_n を変えた 2 点で不変（発行が入力量に比例しない＝オーダーの表明）。
    """
    # Arrange
    spy = _CountingTable(marod_bands._KIND_AGGREGATORS)
    monkeypatch.setattr(marod_bands, "_KIND_AGGREGATORS", spy)
    values = np.linspace(0.0, 1.0, n)

    # Act
    for _ in range(calls):
        marod_bands.rolling_causal_fast(values, window_n, "mean")

    # Assert
    assert spy.lookups - calls == 0
