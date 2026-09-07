"""KIND_INVOKERS — 引数渡し規約（記述子の kind）ごとの呼出器（call_binding から分離・ISSUE-502 段階 4B）。

責務は 1 つだけである: **記述子と callable を受け取り、規約どおりに add_* を呼ぶ**。

引数渡し規約の種別は指標記述子の宣言として ``call_binding._TABLE`` が持ち、CallBinding の
invoke メソッドは ``call_binding._INVOKERS`` 表を引くだけで分岐を持たない（新しい引数渡し規約は
表へ 1 行足すだけで足りる＝invoke 本体を改変しない・SOLID 是正 OCP-2）。

本モジュールは **どの指標にも属さない汎用規約**（kw＝df 以降キーワード専用）だけを持つ。
特定の指標だけが要する規約（例: tgp_btlm の btlm 規約＝fitter を第 3 位置引数へ）は協働子
``adapter.compute.bindings`` 配下が所有し、``call_binding._INVOKERS`` が束ねる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from adapter.compute.param_binding import bind_kwargs


@dataclass(frozen=True)
class Invoker:
    """1 つの引数渡し規約（記述子の kind）の実体。

    consumes : 呼出側（CallBinding の invoke）自身が消費する param 名（add_* の kwarg ではない）。
    call     : ``(spec, callable_, chart, df, kw, consumed) -> None``。
    """

    consumes: frozenset[str]
    call: Callable[..., None]


def identity_preprocess(df: Any, kw: dict[str, Any]) -> dict[str, Any]:
    """preprocess 未宣言の指標が使う既定フック（変換なし）。"""
    del df
    return kw


def call_kw(spec, callable_, chart, df, kw, consumed) -> None:
    """add_*(chart, df, **kw)（df 以降キーワード専用）。

    指標固有の前処理は記述子の preprocess 宣言へ委譲する（compute_id 直判定は
    どこにも無い・ISSUE-097 🟡-7）。未宣言の指標は恒等フックへ落ちる＝変換なし。
    """
    del consumed
    kw = bind_kwargs(callable_, kw)
    kw = (spec.get("preprocess") or identity_preprocess)(df, kw)
    callable_(chart, df, **kw)


#: ``kw`` 規約（指標固有の知識を持たない既定）。
KW_INVOKER = Invoker(frozenset(), call_kw)
