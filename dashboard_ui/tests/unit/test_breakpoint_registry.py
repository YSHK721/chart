"""§5.5.1 価格へ逆算できる指標の集合を**構造**で表すことを固定する。

`tickvol` の除外は列挙で書かない（§8 OCP / LSP）。`breakpoints()` を提供できない指標は
レジストリに**キーが無い**という形で現れ、`resolve()` が None を返す。除外リストを持つと、
指標が増えたときに 2 か所（対象表と除外表）を直す必要が生まれ、片方だけ腐る。
"""
from __future__ import annotations

import pytest

from dashboard_ui.adapter.breakpoints import BreakpointRegistry
from dashboard_ui.domain.bar import Bar

#: 形成中足（走行 H / L を持つ）。
FORMING = Bar(time=1_700_000_000, open=100.0, high=120.0, low=80.0, close=100.0)


@pytest.mark.parametrize(
    "indicator_id", ["ma_marod", "btlm_trail_marod", "profit_rsi"]
)
def test_the_invertible_indicators_resolve_to_a_breakpoint_source(indicator_id: str) -> None:
    source = BreakpointRegistry().resolve(indicator_id)

    assert source is not None
    assert source.breakpoints(
        bar=FORMING, params={"source": "hlc3", "apply": 5}, prev_value=None
    ) == (80.0, 120.0)


@pytest.mark.parametrize("indicator_id", ["tickvol", "moving_averages", "cvfe"])
def test_the_indicators_without_a_price_inverse_have_no_key(indicator_id: str) -> None:
    assert BreakpointRegistry().resolve(indicator_id) is None


def test_the_invertible_ids_match_what_resolve_answers() -> None:
    """§7.1 の契約検査が読む集合が、`resolve` の挙動と同一であること（第 2 定義を作らない）。"""
    registry = BreakpointRegistry()

    ids = registry.invertible_ids()

    assert ids == frozenset({"ma_marod", "btlm_trail_marod", "profit_rsi"})
    assert all(registry.resolve(indicator_id) is not None for indicator_id in ids)


def test_every_source_answers_the_previous_value_question() -> None:
    """LSP: どの source も同じ面を持つ（呼ぶ側が指標名で分岐しない）。

    marod 系は上下分岐を持たないため None（＝分岐の高さは要らない）を返す。
    """
    registry = BreakpointRegistry()

    marod = registry.resolve("ma_marod").previous_value(
        bar=FORMING, params={"source": "hlc3"}
    )
    rsi = registry.resolve("profit_rsi").previous_value(
        bar=FORMING, params={"apply": 5}
    )

    assert marod is None
    assert rsi == (120.0 + 80.0 + 100.0) / 3.0
