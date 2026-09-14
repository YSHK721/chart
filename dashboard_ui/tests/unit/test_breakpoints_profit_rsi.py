"""§5.5.2 `profit_rsi` の区分の境目（適用価格の折れ ＋ 上下分岐）を固定する。

実測（§5.5.2・参照実装 `tools/measure/issue449/probe_inverse.py`）: `profit_rsi` の `v(C)` は
**区分 6**（marod 系は 3）。増えるぶんは RSI の差分 `x_t - x_{t-1}` の符号で式が変わる
**上下分岐**である。分岐の位置は `x(C) = x_{t-1}` を満たす終値 C であり、`x(C)` は走行 H / L で
折れる区分アフィンなので、区分ごとに 1 つずつ候補が立つ（折れ 2 点 ＋ 候補 3 点 ＝ 5 つの
境目 ＝ 6 区分）。

検出力の自己検査: 分岐の境目を落とすと当てはめ残差が桁違いに大きくなることも同じ検定で
固定する（§7.1 の失敗形「赤くならないテスト」を踏まない）。
"""
from __future__ import annotations

import numpy as np

from dashboard_ui.adapter.breakpoints.profit_rsi import ProfitRsiBreakpoints
from dashboard_ui.domain.bar import Bar
from dashboard_ui.domain.price_value_map import PriceValueMap

#: 走行極値のはっきりした形成中足（H0 = 120 / L0 = 80）。
FORMING = Bar(time=1_700_000_000, open=100.0, high=120.0, low=80.0, close=100.0)

#: 前バーの適用価格（上下分岐の高さ）。
PREV = 101.0


def hlc3(bar: Bar, close: float) -> float:
    """終値候補を置いたときの hlc3（参照実装 probe_inverse.py:104-106 と同一規約）。"""
    return (max(bar.high, close) + min(bar.low, close) + close) / 3.0


def source() -> ProfitRsiBreakpoints:
    """適用価格を注入した実装（既定の解決は別の検定で見る）。"""
    return ProfitRsiBreakpoints(applied_price=lambda bar, close, params: hlc3(bar, close))


def branched_forward(bar: Bar, prev: float):
    """上下分岐を持つ前進評価（差分の符号で式が変わる＝RSI と同型）。"""

    def forward(close: float) -> float:
        x = hlc3(bar, close)
        if x > prev:
            return (2.0 * x + 300.0) / (x + 200.0)
        return (3.0 * x + 100.0) / (x + 150.0)

    return forward


def max_residual(cuts, bar: Bar, prev: float) -> float:
    forward = branched_forward(bar, prev)
    fitted = PriceValueMap.fit(forward, cuts, span=max(bar.high - bar.low, 1.0))
    probes = np.linspace(bar.low - 3.0 * 40.0, bar.high + 3.0 * 40.0, 121)
    return max(abs(fitted.value_at(float(c)) - forward(float(c))) for c in probes)


def test_without_a_previous_value_only_the_folds_remain() -> None:
    """前バーの適用価格が無ければ分岐の位置は決まらない（推測で置かない）。"""
    cuts = source().breakpoints(bar=FORMING, params={"apply": 5}, prev_value=None)

    assert cuts == (80.0, 120.0)


def test_the_branch_candidates_are_added_to_the_folds() -> None:
    """区分 6 ＝ 折れ 2 点 ＋ 区分ごとの分岐候補 3 点。

    `x(C) = prev` を区分ごとに解く（hlc3・走行 H0=120 / L0=80 / prev=101）:
        C < L0        : x = (H0 + 2C) / 3      → C = (3*prev - H0) / 2
        L0 <= C <= H0 : x = (H0 + L0 + C) / 3  → C = 3*prev - H0 - L0
        C > H0        : x = (2C + L0) / 3      → C = (3*prev - L0) / 2
    """
    cuts = source().breakpoints(bar=FORMING, params={"apply": 5}, prev_value=PREV)

    assert cuts == (
        80.0,                           # 走行 L
        (3.0 * PREV - 120.0) / 2.0,     # 91.5  （C < L0 の解）
        3.0 * PREV - 120.0 - 80.0,      # 103.0 （L0 <= C <= H0 の解）
        (3.0 * PREV - 80.0) / 2.0,      # 111.5 （C > H0 の解）
        120.0,                          # 走行 H
    )


def test_the_cuts_are_sorted_and_unique() -> None:
    """境界値: 分岐が折れと重なるときは同じ点を 2 回返さない（区分が潰れない）。"""
    prev_at_low = hlc3(FORMING, 80.0)      # C = L0 が分岐点になる prev

    cuts = source().breakpoints(
        bar=FORMING, params={"apply": 5}, prev_value=prev_at_low
    )

    assert list(cuts) == sorted(set(cuts))
    assert 80.0 in cuts


def test_the_fit_reproduces_a_branched_forward_evaluation() -> None:
    """折れと分岐の両方を境目に入れれば、当てはめは前進評価に一致する。"""
    cuts = source().breakpoints(bar=FORMING, params={"apply": 5}, prev_value=PREV)

    residual = max_residual(cuts, FORMING, PREV)

    assert residual < 1e-9


def test_dropping_the_branch_breaks_the_fit() -> None:
    """検出力: 分岐を落とすと（折れだけでは）残差が桁違いに大きくなる。"""
    with_branch = max_residual(
        source().breakpoints(bar=FORMING, params={"apply": 5}, prev_value=PREV),
        FORMING, PREV,
    )
    folds_only = max_residual((80.0, 120.0), FORMING, PREV)

    assert folds_only > with_branch * 1e6


def test_the_previous_value_is_the_applied_price_of_the_previous_bar() -> None:
    """分岐の高さは前バーの**適用価格**である（終値ではない）。"""
    previous = Bar(time=1_699_999_940, open=99.0, high=110.0, low=90.0, close=100.0)

    value = source().previous_value(bar=previous, params={"apply": 5})

    assert value == (110.0 + 90.0 + 100.0) / 3.0


def test_the_default_applied_price_follows_the_indicator_own_mapping() -> None:
    """既定の適用価格は `profit_rsi` 自身の適用価格の写像に従う（写しを持たない）。

    params の "apply" が 5 なら hlc3（TYPICAL）。common.applied_price の列挙で 5 は MEDIAN で
    あって TYPICAL ではないため、素の列挙で読み替えると**別の価格**になる。
    """
    default = ProfitRsiBreakpoints()

    typical = default.previous_value(bar=FORMING, params={"apply": 5})
    median = default.previous_value(bar=FORMING, params={"apply": 4})

    assert typical == (120.0 + 80.0 + 100.0) / 3.0
    assert median == (120.0 + 80.0) / 2.0
