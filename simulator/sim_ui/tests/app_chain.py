"""包み手の連鎖を**深さを数えずに**辿る（テスト支援・ISSUE-508 段階 4）。

なぜ在るか（実測に基づく是正）:
    「wrapper を足す前の面」を得るために各検定が `app.inner.inner` と**ホップ数を手書き**
    していた。段階 4 で trace 層（6 本目）を挟んだ結果、同じ式が別の層を指すようになり、
    「増分は 1 本だけ」を測っていた検定が**測りたいものと違う対象**を比べて緑になりかけた
    （実際 `test_serve_sim_settings_schema.py` は 200 != 200 で落ちた）。

    ホップ数は連鎖の長さという**別の事実**に依存しており、層を 1 本足すたびに全検定の
    数字を数え直すことになる。数えるのをやめ、**探す層のクラスで指す**。
    これは `sim_tabs_view.test.js` の手書き期待値と同型の是正である。
"""
from __future__ import annotations

from typing import Any


def wrapper_of(app: Any, app_class: type) -> Any:
    """``app`` から `inner` を辿って ``app_class`` の実体を返す。

    事前条件: ``app`` は `inner` プロパティで内側を公開する包み手の連鎖の外端。
    事後条件: ``app_class`` のインスタンスを返す（自分自身も候補に含む）。
    例外: 連鎖に見つからなければ `AssertionError`（黙って `None` を返さない——
        `None` を返すと呼出側が「面が無い」と読んで検定が空振りする）。
    """
    seen: "list[str]" = []
    node = app
    while node is not None:
        seen.append(type(node).__name__)
        if isinstance(node, app_class):
            return node
        node = getattr(node, "inner", None)
    raise AssertionError(
        f"{app_class.__name__} が包み手の連鎖に見つかりません（辿った順: {seen}）"
    )


def inside(app: Any, app_class: type) -> Any:
    """``app_class`` の**内側**（その層が包んでいる面）を返す。

    「その層を足す前の面」がこれである。合成根を書き写して組み直さない——組み直すと
    合成根の複製になり、比較対象が「本物の内側」であることを保証できない。
    """
    wrapper = wrapper_of(app, app_class)
    inner = getattr(wrapper, "inner", None)
    assert inner is not None, f"{app_class.__name__} が内側を公開していません"
    return inner
