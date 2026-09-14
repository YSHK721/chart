"""受理集合の取得元を bridge から束ねる口（是正レビュー Y-1）。

## なぜ在るか

「bridge から受理集合を読む手順」は受理集合の読取関数を包む 1 行のラムダである。同じ 1 行が
供給面の口・前方評価の口の 2 箇所に既在で、Composition Root がそれを 3 枚目として書き写して
いた。写しが増えるほど「どこで絞っているか」の所有者が散り、片方だけ直した日に**片方の口
だけがライブ core を触りに行く**（素材を固定した検定で、param の絞り込みだけが実データを
読む形になる）。手順は型の側が持ち、呼ぶ側は相手（bridge）を渡すだけにする。

## 排他にする理由

source と bridge は同じ 1 つのこと（受理集合をどこから取るか）を指す。両方を受けると
「どちらが勝つか」という規則が新たに生まれ、その規則を知らない呼び出し側が静かに
無視される方を渡す。両方来たら**その場で落とす**（規則を作らない）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dashboard_ui.adapter.gateway.param_scopes import ParamScopes

#: ライブ core が配る受理集合と同じ形（compute_id → variant → 受理 param 名）。
SCOPES = {"ma_marod": {"default": ("source", "ma_type", "length")}}


def _bridge(calls: "list[int]") -> SimpleNamespace:
    """受理集合を公開する bridge（解決の発行回数を数える）。"""

    def catalog_param_scopes():
        calls.append(1)
        return SCOPES

    return SimpleNamespace(catalog_param_scopes=catalog_param_scopes)


def test_the_scopes_come_from_the_injected_bridge() -> None:
    """渡した bridge の受理集合で params が絞られる。"""
    scopes = ParamScopes(bridge=_bridge([]))

    scoped = scopes.scoped(
        indicator_id="ma_marod", variant="default",
        params={"length": 50, "wait_for_close": True},
    )

    assert scoped == {"length": 50}


def test_an_unknown_variant_is_passed_through_unchanged() -> None:
    """受理集合を持たない variant は素通しする（既定の契約と同じ）。"""
    scopes = ParamScopes(bridge=_bridge([]))

    scoped = scopes.scoped(
        indicator_id="ma_marod", variant="nope", params={"wait_for_close": True},
    )

    assert scoped == {"wait_for_close": True}


def test_the_bridge_is_read_once_no_matter_how_many_times_it_is_asked() -> None:
    """計算量（無駄の不在）: 絞る回数を増やしても受理集合の解決は増えない。

    オーダーの表明は 2 点（3 / 30）で固定する。回数そのものは焼き込まない。
    """
    additional = {}
    for asks in (3, 30):
        calls: "list[int]" = []
        scopes = ParamScopes(bridge=_bridge(calls))
        scopes.scoped(indicator_id="ma_marod", variant="default", params={})
        warmed = len(calls)
        for _ in range(asks):
            scopes.scoped(indicator_id="ma_marod", variant="default", params={})
        additional[asks] = len(calls) - warmed
        assert warmed > 0

    assert additional[3] == 0
    assert additional[30] == 0


def test_giving_both_a_source_and_a_bridge_is_refused() -> None:
    """同じ 1 つのことを 2 通りで渡されたら、どちらを勝たせるかを決めずに落とす。"""
    # 文言まで見るのは、キーワード自体が未知でも TypeError になるためである
    #   （それでは「排他を実装した」証拠にならない＝弱い assertion）。
    with pytest.raises(TypeError, match="同時"):
        ParamScopes(source=lambda: SCOPES, bridge=_bridge([]))


def test_passing_bridge_none_is_the_same_as_passing_nothing() -> None:
    """既定（どちらも渡さない）と bridge=None は同じ手順に落ちる。

    Composition Root は bridge を素通しで渡し、本番ではそれが None である。None が
    「渡さない」と同じ扱いにならないと、**本番経路だけが**別の手順に落ちて、ライブ core の
    単一ソースを読まなくなる（検定側は bridge を渡すので誰も気付かない）。
    """
    explicit_none = ParamScopes(bridge=None)
    nothing = ParamScopes()

    assert explicit_none._source is nothing._source
