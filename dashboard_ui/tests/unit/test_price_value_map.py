"""§5.5.2/§5.5.3 区分メビウス `v = (aC + b) / (C + d)` の当てはめと逆写像を固定する。

参照実装: `tools/measure/issue449/probe_heatmap.py:155-176`（`breakpoints` / `fit_mobius` / `ev`）。
実測（§5.5.2）で 3 指標とも `v(C)` は区分メビウスであり、3 点当てはめの残差は 6.0e-14〜3.9e-12、
全区分で単調増加だった。したがって:

  - 逆写像は近似ではなく**閉形式で厳密** `C = (b - v·d) / (v - a)`。反復探索は要らない。
  - 逆関数の数式を**指標ごとに手書きしない**。逆写像は本モジュール 1 か所が所有する（§8 単一ソース）。
"""
from __future__ import annotations

import pytest

from dashboard_ui.domain.price_value_map import (
    MobiusFitError,
    MobiusPiece,
    PriceValueMap,
)


def _mobius(a: float, b: float, d: float):
    return lambda price: (a * price + b) / (price + d)


#: 区分の境目 100 で**連続かつ単調増加**に繋がる 2 区分（§5.5.2 の実測どおりの形）。
_LOW = _mobius(2.0, 300.0, 200.0)
_HIGH = _mobius(3.0, -650.0 / 3.0, -50.0)


def _piecewise(price: float) -> float:
    return _LOW(price) if price <= 100.0 else _HIGH(price)


class _ForwardSpy:
    """前進評価の発行回数を数える Test Spy（§7 の計算量表明と同じ面を数える）。"""

    def __init__(self, function) -> None:
        self._function = function
        self.calls = 0

    def __call__(self, price: float) -> float:
        self.calls += 1
        return self._function(price)


class TestFit:
    def test_a_mobius_forward_evaluation_is_recovered_exactly(self) -> None:
        truth = _mobius(2.0, 300.0, 200.0)

        fitted = PriceValueMap.fit(truth, [100.0], span=50.0)

        for price in (10.0, 55.5, 120.0):
            assert fitted.value_at(price) == pytest.approx(truth(price), rel=1e-12)

    def test_a_fit_without_any_breakpoint_is_rejected(self) -> None:
        """境目が無いと両端が ±inf になり探針を置けない。参照実装は必ず走行 L/H を返す。"""
        with pytest.raises(ValueError):
            PriceValueMap.fit(_mobius(2.0, 300.0, 200.0), [], span=50.0)

    def test_each_breakpoint_starts_a_new_piece(self) -> None:
        fitted = PriceValueMap.fit(_mobius(2.0, 300.0, 200.0), [100.0, 140.0], span=50.0)

        assert len(fitted.pieces) == 3

    def test_a_piecewise_function_is_recovered_on_both_sides_of_the_break(self) -> None:
        """区分の境目（適用価格 hlc3 の折れ）を跨いでも、各区分で厳密に一致する。"""
        fitted = PriceValueMap.fit(_piecewise, [100.0], span=50.0)

        for price in (20.0, 99.0, 101.0, 180.0):
            assert fitted.value_at(price) == pytest.approx(_piecewise(price), rel=1e-10)

    def test_the_fit_spends_the_minimum_three_probes_per_piece(self) -> None:
        """係数は 3 つ（a, b, d）なので 1 区分 3 点が最小。過剰な探針を発行しない。"""
        spy = _ForwardSpy(_mobius(2.0, 300.0, 200.0))

        fitted = PriceValueMap.fit(spy, [100.0], span=50.0)

        assert spy.calls == 3 * len(fitted.pieces)

    def test_evaluating_prices_issues_no_further_forward_evaluation(self) -> None:
        """§5.5.4 の核心: 係数を決めた後の価格評価は前進評価を一切呼ばない。"""
        spy = _ForwardSpy(_mobius(2.0, 300.0, 200.0))
        fitted = PriceValueMap.fit(spy, [100.0], span=50.0)
        after_fit = spy.calls

        for price in range(50, 150):
            fitted.value_at(float(price))

        assert spy.calls == after_fit

    def test_breakpoints_are_normalised(self) -> None:
        fitted = PriceValueMap.fit(
            _mobius(2.0, 300.0, 200.0), [140.0, 100.0, 100.0], span=50.0)

        assert len(fitted.pieces) == 3
        assert [piece.lo for piece in fitted.pieces][1:] == [100.0, 140.0]

    def test_a_degenerate_piece_is_skipped(self) -> None:
        """境界値: 幅がほぼ 0 の区分には 3 点を置けない（当てはめを試みない）。"""
        fitted = PriceValueMap.fit(
            _mobius(2.0, 300.0, 200.0), [100.0, 100.0 + 1e-12], span=50.0)

        assert len(fitted.pieces) == 2

    def test_a_constant_forward_evaluation_is_reported_instead_of_guessed(self) -> None:
        """定数は係数が一意に決まらない（特異）。無言で 1 解を選ばず失敗を明示する。"""
        with pytest.raises(MobiusFitError):
            PriceValueMap.fit(lambda price: 42.0, [100.0], span=50.0)

    def test_a_non_finite_breakpoint_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            PriceValueMap.fit(_mobius(2.0, 300.0, 200.0), [float("inf")], span=50.0)

    @pytest.mark.parametrize("span", [0.0, -1.0])
    def test_a_non_positive_span_is_rejected(self, span: float) -> None:
        with pytest.raises(ValueError):
            PriceValueMap.fit(_mobius(2.0, 300.0, 200.0), [], span=span)


class TestInverse:
    def test_the_inverse_is_the_exact_closed_form(self) -> None:
        truth = _mobius(2.0, 300.0, 200.0)
        fitted = PriceValueMap.fit(truth, [100.0], span=50.0)

        for price in (30.0, 99.0, 101.0, 170.0):
            assert fitted.price_at(truth(price)) == pytest.approx(price, rel=1e-9)

    def test_a_value_no_piece_can_produce_has_no_price(self) -> None:
        """候補が無いときは None（§5.5.5 の「空にして色を置かない」の土台）。"""
        fitted = PriceValueMap.fit(_mobius(2.0, 300.0, 200.0), [100.0], span=50.0)

        assert fitted.price_at(1e9) is None

    def test_the_pole_of_the_inverse_has_no_price(self) -> None:
        """v = a は逆写像の極（§10 の「価格が発散する」形）。無限大を返さない。"""
        piece = MobiusPiece(lo=0.0, hi=1000.0, a=2.0, b=300.0, d=200.0)

        assert piece.price_at(2.0) is None

    def test_the_inverse_picks_the_piece_that_actually_produces_the_value(self) -> None:
        """隣の区分の**外挿**が同じ値を作れても、探針を置いた範囲の解を採る。"""
        fitted = PriceValueMap.fit(_piecewise, [100.0], span=50.0)

        assert fitted.price_at(_piecewise(150.0)) == pytest.approx(150.0, rel=1e-9)
        assert fitted.price_at(_piecewise(20.0)) == pytest.approx(20.0, rel=1e-9)


class TestMonotonicity:
    def test_an_increasing_map_is_reported_as_increasing(self) -> None:
        """§5.5.2: 全区分で単調増加だから、価格の交差判定が指標値の交差と同値になる。"""
        fitted = PriceValueMap.fit(_mobius(2.0, 300.0, 200.0), [100.0], span=50.0)

        assert fitted.is_monotonic_increasing() is True

    def test_a_piecewise_map_that_is_increasing_everywhere_is_reported_as_increasing(
        self,
    ) -> None:
        fitted = PriceValueMap.fit(_piecewise, [100.0], span=50.0)

        assert fitted.is_monotonic_increasing() is True

    def test_a_decreasing_map_is_not_reported_as_increasing(self) -> None:
        fitted = PriceValueMap.fit(_mobius(1.0, 900.0, 200.0), [100.0], span=50.0)

        assert fitted.is_monotonic_increasing() is False

    def test_a_piece_whose_pole_lies_inside_it_is_not_monotonic(self) -> None:
        piece = MobiusPiece(lo=-300.0, hi=300.0, a=2.0, b=300.0, d=200.0)

        assert piece.is_increasing() is False
