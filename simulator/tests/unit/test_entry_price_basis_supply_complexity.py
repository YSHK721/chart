"""建値基準の**出所を 1 つにした後**の計算量（絶対命令 2026-08-28・ISSUE-533 段階 2）。

段階 2 は「推定建値の系列を設定から受け取る」形をやめ、**包んだ戦略の宣言から導く**形に
した。ここで入り込みうる浪費は状態検証では原理的に落ちない:

    導出を発注ごと・足ごとにやり直しても、宣言は run のあいだ変わらないので**出力は
    1 ビットも変わらない**。速いか遅いかだけが変わる。

したがって測るのは時間ではなく**回数**であり、固定するのは回数そのものではなく
**無駄の不在**である。

固定する不変条件:
    1. 発行 − 使用 = 0。「発行」は宣言を導いた回数、「使用」は系列名を要する実体の数
       （包んだ戦略 1 つにつき 1 つ）。
    2. オーダーの表明: 足の数・1 足あたりの発注数をそれぞれ 3 倍にしても、発行は増えない。
       **回数そのものは期待値に焼き込まない**（「N 回呼ばれること」を固定すると浪費が
       仕様へ昇格する）。

参照実装の同型: `simulator/tests/unit/test_entry_price_basis_complexity.py`（段階 1）。
"""
from __future__ import annotations

from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
from simulator.tests.sizing_declaration_fixtures import (
    Account,
    DeclaringStrategy,
    SeriesSpy,
    VolumeConstraints,
)
from simulator.usecase.sizing_models import SizingConfig

#: 規模の 2 点（片方が他方の 3 倍）。値そのものは表明に現れない。
_SMALL_BARS = 4
_LARGE_BARS = 12
_SMALL_ORDERS = 1
_LARGE_ORDERS = 3

#: MC を軽くする（アルゴリズムは同一・決定性はシード固定で保たれる）。
_SIZING = SizingConfig(enabled=True, sims=8, seed=1)


def _measure(*, bars: int, orders_per_bar: int) -> dict:
    """1 つの戦略を包んで ``bars`` 本走らせたときの発行・使用・要求系列。

    キー: issued（宣言を導いた回数）・used（系列名を要する実体の数）・
    asked（実際に問われた系列の集合）・orders（出た発注の総数＝空振り防止）。
    """
    inner = DeclaringStrategy("current_open", orders_per_bar=orders_per_bar)
    factory = build_sizing_decorator(_SIZING, symbol_spec=VolumeConstraints())
    wrapped = factory(inner)

    spy = SeriesSpy()
    orders = 0
    for bar_index in range(bars):
        orders += len(wrapped.on_new_bar(bar_index, spy, Account()))

    return {
        "issued": inner.declaration_reads,
        "used": 1,                      # 包んだ戦略は 1 つ＝要る系列名は 1 つ
        "asked": set(spy.asked),
        "orders": orders,
    }


def test_the_declaration_is_derived_once_per_wrapped_strategy() -> None:
    """発行 − 使用 = 0（導いて捨てる宣言が 1 つも無い）。"""
    # Act
    measured = _measure(bars=_LARGE_BARS, orders_per_bar=_LARGE_ORDERS)

    # Assert
    assert measured["orders"] > 0            # 空振り防止（現に発注が出ている）
    assert measured["asked"] == {"open"}     # 空振り防止（導いた答えを実際に使っている）
    assert measured["issued"] - measured["used"] == 0


def test_the_derivation_does_not_grow_with_the_run() -> None:
    """足の数も 1 足あたりの発注数も 3 倍にして、宣言の導出が増えない（オーダーの表明）。

    **回数そのものは焼き込まない**——等しいことだけを表明する。
    """
    # Act
    small = _measure(bars=_SMALL_BARS, orders_per_bar=_SMALL_ORDERS)
    more_bars = _measure(bars=_LARGE_BARS, orders_per_bar=_SMALL_ORDERS)
    more_orders = _measure(bars=_SMALL_BARS, orders_per_bar=_LARGE_ORDERS)

    # Assert
    assert more_bars["orders"] == small["orders"] * 3      # 空振り防止（規模が現に違う）
    assert more_orders["orders"] == small["orders"] * 3
    assert more_bars["issued"] == small["issued"]
    assert more_orders["issued"] == small["issued"]
