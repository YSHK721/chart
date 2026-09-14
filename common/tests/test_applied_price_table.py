"""`common.applied_price` の種別列挙が **単一表**に閉じることを固定する（ISSUE-479 Wave2 C-5）。

背景（実測）: 同じ種別集合が 3 箇所で列挙されていた —— AppliedPrice の enum 定義、
applied_price の if 連鎖、SOURCE_TO_APPLIED の写像。さらに docstring 2 箇所が「7 種」と
書かれており、OHLC4 追加時に更新されず陳腐化していた（実数は 8 種）。

本ファイルが固定するもの:
    1. ディスパッチャ本体に種別ごとの分岐が残っていないこと（AST）
    2. 3 つの列挙の件数が一致すること（片方だけに種別が足された状態の検出）
    3. docstring が実数と食い違わないこと（「7 種」の再発防止）
    4. 統合前の出力・例外文言の bit 等価（digest 凍結・8 種 x 入力型 4 通り x 配列長 2 点）
    5. 1 回の呼び出しで発行される抽出関数が 1 個（全エントリ評価へ退化しない＝計算量）
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import pathlib

import numpy as np
import pytest

applied_price_mod = importlib.import_module("common.applied_price")

_SOURCE_PATH = pathlib.Path(applied_price_mod.__file__)
_TABLE_NAME = "_APPLIED"
_DISPATCHER_NAME = "applied_price"


def _module_tree() -> ast.Module:
    return ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))


def _dispatcher_node(tree: ast.Module) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == _DISPATCHER_NAME:
            return node
    raise AssertionError(f"{_DISPATCHER_NAME} が見つからない")


def test_dispatcher_body_has_no_per_kind_branch() -> None:
    """ディスパッチャ本体に AppliedPrice.<種別> 参照が 0 件（表引きへ一本化）。"""
    # Arrange
    node = _dispatcher_node(_module_tree())

    # Act: 本体（docstring 含む）に現れる AppliedPrice.<attr> の行番号。
    hits = [
        sub.lineno
        for sub in ast.walk(node)
        if isinstance(sub, ast.Attribute)
        and isinstance(sub.value, ast.Name)
        and sub.value.id == "AppliedPrice"
    ]

    # Assert
    assert hits == [], f"種別分岐がディスパッチャ本体に残存: {_SOURCE_PATH.name}:{hits}"


def test_the_three_enumerations_have_the_same_number_of_kinds() -> None:
    """enum / 単一表 / ソース写像の件数が一致する（片方だけ増えた状態を検出）。"""
    # Arrange / Act
    n_enum = len(applied_price_mod.AppliedPrice)
    n_table = len(applied_price_mod._APPLIED)
    n_source_map = len(applied_price_mod.SOURCE_TO_APPLIED)

    # Assert
    assert n_enum == n_table == n_source_map == 8


def test_docstrings_do_not_claim_a_stale_kind_count() -> None:
    """docstring が「7 種」と書いていない（OHLC4 追加で 8 種になった実数と一致させる）。"""
    # Arrange: 種別数を語る docstring 2 箇所（モジュール概要とディスパッチャ）。
    docs = {
        "module": applied_price_mod.__doc__,
        "applied_price": applied_price_mod.applied_price.__doc__,
    }
    stale_count = f"{len(applied_price_mod.AppliedPrice) - 1} 種"

    # Act
    stale = sorted(name for name, doc in docs.items() if doc and stale_count in doc)

    # Assert
    assert stale == [], f"docstring の種別数が実数と食い違っている: {stale}"


def test_source_to_applied_is_unchanged() -> None:
    """ソース写像のキー・値・挿入順が移設前と完全に同一。"""
    assert list(applied_price_mod.SOURCE_TO_APPLIED.items()) == [
        ("close", applied_price_mod.AppliedPrice.CLOSE),
        ("open", applied_price_mod.AppliedPrice.OPEN),
        ("high", applied_price_mod.AppliedPrice.HIGH),
        ("low", applied_price_mod.AppliedPrice.LOW),
        ("hl2", applied_price_mod.AppliedPrice.MEDIAN),
        ("hlc3", applied_price_mod.AppliedPrice.TYPICAL),
        ("hlcc4", applied_price_mod.AppliedPrice.WEIGHTED),
        ("ohlc4", applied_price_mod.AppliedPrice.OHLC4),
    ]


def test_applied_price_matches_the_frozen_digest() -> None:
    """統合前の出力と bit 一致（8 種 x kind 型 4 通り x 配列長 2 点）。

    kind の受理形（enum member / int / float / np.int64）は既存の呼び出し側が実際に
    渡す形であり、表引きへ替えても等価に解決されることを固定する。
    """
    # Arrange / Act
    digest = hashlib.sha256()
    for n in (7, 313):
        rng = np.random.default_rng(4242 + n)
        open_, high, low, close = (rng.normal(100.0, 5.0, n) for _ in range(4))
        for value in range(1, 9):
            for kind in (
                applied_price_mod.AppliedPrice(value),
                int(value),
                float(value),
                np.int64(value),
            ):
                digest.update(
                    applied_price_mod.applied_price(kind, open_, high, low, close).tobytes()
                )

    # Assert
    assert digest.hexdigest() == (
        "ebf8818b5a4c982b447d5412f5f2e885ecaebd78cdd4e8d56063eb823e0c4195"
    )


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        (0, "未知の適用価格種別です: 0"),
        (9, "未知の適用価格種別です: 9"),
        ("close", "未知の適用価格種別です: 'close'"),
        (None, "未知の適用価格種別です: None"),
        ([1], "未知の適用価格種別です: [1]"),
    ],
    ids=["below_range", "above_range", "str", "none", "unhashable"],
)
def test_unknown_kind_raises_the_same_valueerror_message(kind, message: str) -> None:
    """未知 kind は文言まで同一の ValueError（非ハッシュ可能値も含む）。"""
    arrays = (np.ones(3),) * 4
    with pytest.raises(ValueError) as excinfo:
        applied_price_mod.applied_price(kind, *arrays)
    assert str(excinfo.value) == message


@pytest.mark.parametrize("n", [10, 1000])
def test_one_call_issues_exactly_one_extractor(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    """計算量テスト: 発行した抽出関数 − 出力に使った抽出関数 = 0。

    「使った抽出」は 1 呼び出しにつき 1 件（1 呼び出しは 1 種別しか出力しない）。
    全エントリを評価してから 1 個を選ぶ実装へ退化すると発行が 8 になり赤になる。
    回数を焼き込まず**無駄の不在**を固定し、配列長 10/1000 の 2 点で不変（オーダーの表明）。
    """
    # Arrange: 表の全エントリの抽出関数を計数ラッパで包む。
    issued: list[str] = []

    def _counting(src: str, fn):
        def _wrapped(*args, **kwargs):
            issued.append(src)
            return fn(*args, **kwargs)

        return _wrapped

    spy_table = {
        kind: (src, _counting(src, fn))
        for kind, (src, fn) in applied_price_mod._APPLIED.items()
    }
    monkeypatch.setattr(applied_price_mod, _TABLE_NAME, spy_table)
    arrays = tuple(np.linspace(1.0, 2.0, n) for _ in range(4))

    # Act
    applied_price_mod.applied_price(applied_price_mod.AppliedPrice.TYPICAL, *arrays)

    # Assert: 使った抽出は 1 件（hlc3）。発行 − 使用 = 0。
    assert len(issued) - 1 == 0
    assert issued == ["hlc3"]
