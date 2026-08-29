"""§5.5.2 marod 系の区分の境目（走行 H / L の折れ）を固定する。

境目の定義は参照実装 `tools/measure/issue449/probe_inverse.py`（区分 `C<L` / `L<=C<=H` /
`C>H` の 3 分割）の実測に従う。適用価格（hlc3 等）は `C` が走行 H を越えれば高値も動き、
走行 L を割れば安値も動くため、その 2 点で傾きが折れる。

検出力の自己検査（§7.1 の失敗形「赤くならないテスト」を踏まない）: 折れを 1 つ落とした
境目では当てはめ残差が桁違いに大きくなることも同じ検定で固定する。境目が正しいことを
「実装がそう書いてある」ではなく**残差**で示す。
"""
from __future__ import annotations

import numpy as np

from dashboard_ui.adapter.breakpoints.marod import MarodBreakpoints
from dashboard_ui.domain.bar import Bar
from dashboard_ui.domain.price_value_map import PriceValueMap

#: 走行極値のはっきりした形成中足（H0 = 120 / L0 = 80）。
FORMING = Bar(time=1_700_000_000, open=100.0, high=120.0, low=80.0, close=100.0)


def applied_hlc3(bar: Bar, close: float) -> float:
    """終値候補を置いたときの hlc3（参照実装 probe_inverse.py:104-106 と同一規約）。"""
    return (max(bar.high, close) + min(bar.low, close) + close) / 3.0


def forward_of(bar: Bar):
    """hlc3 のメビウス（＝`C` について区分メビウス）。折れは hlc3 の折れだけで決まる。"""

    def forward(close: float) -> float:
        x = applied_hlc3(bar, close)
        return (2.0 * x + 300.0) / (x + 200.0)

    return forward


def max_residual(cuts, bar: Bar) -> float:
    """その境目で当てはめたときの、探針以外の点での最大残差。"""
    forward = forward_of(bar)
    fitted = PriceValueMap.fit(forward, cuts, span=max(bar.high - bar.low, 1.0))
    probes = np.linspace(bar.low - 3.0 * 40.0, bar.high + 3.0 * 40.0, 61)
    return max(abs(fitted.value_at(float(c)) - forward(float(c))) for c in probes)


def test_the_breakpoints_are_the_running_extremes() -> None:
    source = MarodBreakpoints()

    cuts = source.breakpoints(bar=FORMING, params={"source": "hlc3"}, prev_value=None)

    assert cuts == (80.0, 120.0)


def test_the_previous_value_is_not_a_breakpoint_for_marod() -> None:
    """marod は上下分岐を持たない（分岐は profit_rsi 固有・§5.5.2）。"""
    source = MarodBreakpoints()

    with_prev = source.breakpoints(
        bar=FORMING, params={"source": "hlc3"}, prev_value=99.0
    )

    assert with_prev == (80.0, 120.0)


def test_a_flat_bar_yields_a_single_breakpoint() -> None:
    """境界値: 走行 H = L の足では折れが 1 点に縮む（同じ点を 2 回返さない）。"""
    flat = Bar(time=1_700_000_060, open=100.0, high=100.0, low=100.0, close=100.0)
    source = MarodBreakpoints()

    cuts = source.breakpoints(bar=flat, params={"source": "hlc3"}, prev_value=None)

    assert cuts == (100.0,)


def test_the_fit_reproduces_the_forward_evaluation_at_unprobed_prices() -> None:
    """境目が正しければ、探針を置いていない価格でも当てはめが前進評価に一致する。"""
    source = MarodBreakpoints()
    cuts = source.breakpoints(bar=FORMING, params={"source": "hlc3"}, prev_value=None)

    residual = max_residual(cuts, FORMING)

    assert residual < 1e-9


def test_dropping_one_fold_breaks_the_fit() -> None:
    """検出力: 折れを 1 つ落とすと残差が桁違いに大きくなる（検定が空振りしない）。"""
    good = max_residual((80.0, 120.0), FORMING)
    missing_upper = max_residual((80.0,), FORMING)

    assert missing_upper > good * 1e6
