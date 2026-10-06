"""EmaDeviationShort の単体検査（状態検証）と計算量テスト。

正解の出どころ:
    水準の係数は「形成中の EMA から d 上方」の定義（ema_i(P) = a·P + (1−a)·ema[i-1]）へ
    戻して独立に確かめる。戦略の関数で正解を作らない。

計算量テストの観測の境界:
    戦略が指標を読む口は `IndicatorPort`（`on_new_bar` の引数）だけである。Test Spy を
    その口へ渡し、系列の要素読み（`iloc[k]`）を (系列名, 位置) で数える。期待集合は売買規則の
    宣言（「足 i で ema[i-1] と open[i] を読む・売り保有中は読まない」）から作る。
"""
from __future__ import annotations

import math
from collections import Counter
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from simulator.adapter.strategy.ema_deviation_short import EmaDeviationShort, touch_coefficient
from simulator.domain.exceptions import ConfigError
from simulator.usecase.ports import IndicatorPort

EMA_SERIES = "ema"
OPEN_SERIES = "open"


def _config(**overrides: Any) -> dict:
    base = {
        "ma_period": 21, "ema_deviation_pct": 8.0, "lot_size": 1.0,
        "volume_min": 0.1, "volume_max": 100.0, "volume_step": 0.1,
        "stop_loss_points": 500, "take_profit_points": 1000,
        "point_size": 0.1, "digits": 1, "stops_level": 0,
    }
    base.update(overrides)
    return base


class _Registry(IndicatorPort):
    def __init__(self, series: "dict[str, pd.Series]") -> None:
        self._series = series

    def get(self, name: str) -> Any:
        return self._series[name]

    def update(self, bar_index: int) -> None:
        return None


def _account(*sides: str) -> Any:
    return SimpleNamespace(open_positions=[SimpleNamespace(side=s) for s in sides])


def _strategy(series: "dict[str, pd.Series]", **cfg: Any) -> EmaDeviationShort:
    s = EmaDeviationShort()
    s.on_init(_config(**cfg), _Registry(series))
    return s


# ---- 係数（形成中の線に触れる価格） ----

class TestTouchCoefficient:
    @pytest.mark.parametrize(("period", "pct"), [(21, 8.0), (21, 2.0), (5, 8.0), (200, 15.0)])
    def test_the_price_at_the_level_is_exactly_d_above_the_forming_ema(self, period, pct) -> None:
        # Arrange
        a = 2.0 / (period + 1)
        ema_prev = 38_000.0
        # Act
        price = ema_prev * touch_coefficient(period, pct)
        # Assert: 定義へ戻す（形成中 EMA を独立に作り、乖離率を測る）。
        forming = a * price + (1.0 - a) * ema_prev
        assert price / forming - 1.0 == pytest.approx(pct / 100.0, abs=1e-12)

    def test_zero_deviation_is_the_reference_level_ema_prev(self) -> None:
        assert touch_coefficient(21, 0.0) == pytest.approx(1.0, abs=1e-15)

    def test_21_ema_at_8_percent_is_not_ema_prev_times_1_08(self) -> None:
        assert touch_coefficient(21, 8.0) == pytest.approx(1.0887096774, abs=1e-9)

    def test_a_deviation_no_price_can_reach_refuses_to_start(self) -> None:
        # 1 − (1+d)·a <= 0（ma_period=1 では a=1）。
        with pytest.raises(ConfigError):
            touch_coefficient(1, 8.0)


# ---- 発注 ----

class TestOrders:
    def _series(self, ema_prev: float, open_: float) -> "dict[str, pd.Series]":
        return {EMA_SERIES: pd.Series([ema_prev, ema_prev]), OPEN_SERIES: pd.Series([open_, open_])}

    def test_below_the_level_a_sell_limit_is_placed_at_the_level_rounded_up(self) -> None:
        # Arrange
        series = self._series(38_000.0, 38_100.0)
        exact = 38_000.0 * touch_coefficient(21, 8.0)
        expected = math.ceil(exact / 0.1 - 1e-9) * 0.1
        # Act
        (order,) = _strategy(series).on_new_bar(1, _Registry(series), _account())
        # Assert
        assert (order.side, order.kind) == ("sell", "sell_limit")
        assert order.price == pytest.approx(expected, abs=1e-9)
        assert order.price >= exact
        assert order.sl == pytest.approx(order.price + 50.0, abs=1e-9)
        assert order.tp == pytest.approx(order.price - 100.0, abs=1e-9)

    def test_at_or_above_the_level_at_the_open_it_sells_at_market(self) -> None:
        series = self._series(38_000.0, 42_000.0)
        (order,) = _strategy(series).on_new_bar(1, _Registry(series), _account())
        assert (order.side, order.kind, order.price) == ("sell", "market", None)
        assert order.sl == pytest.approx(42_050.0, abs=1e-9)
        assert order.tp == pytest.approx(41_900.0, abs=1e-9)

    def test_while_holding_a_sell_nothing_is_placed(self) -> None:
        series = self._series(38_000.0, 38_100.0)
        assert _strategy(series).on_new_bar(1, _Registry(series), _account("sell")) == []

    def test_the_first_bar_has_no_previous_ema(self) -> None:
        series = self._series(38_000.0, 38_100.0)
        assert _strategy(series).on_new_bar(0, _Registry(series), _account()) == []

    def test_zero_points_place_no_sl_tp(self) -> None:
        series = self._series(38_000.0, 38_100.0)
        s = _strategy(series, stop_loss_points=0, take_profit_points=0)
        (order,) = s.on_new_bar(1, _Registry(series), _account())
        assert (order.sl, order.tp) == (None, None)


# ---- 計算量 ----

class _IlocSpy:
    def __init__(self, name: str, series: pd.Series, reads: "list[tuple[str, int]]") -> None:
        self._name, self._series, self._reads = name, series, reads

    def __getitem__(self, k: int) -> Any:
        self._reads.append((self._name, int(k)))
        return self._series.iloc[k]


class _SeriesSpy:
    def __init__(self, name: str, series: pd.Series, reads: "list[tuple[str, int]]") -> None:
        self.iloc = _IlocSpy(name, series, reads)


class _IndicatorSpy(IndicatorPort):
    def __init__(self, series: "dict[str, pd.Series]") -> None:
        self.reads: "list[tuple[str, int]]" = []
        self._series = series

    def get(self, name: str) -> Any:
        return _SeriesSpy(name, self._series[name], self.reads)

    def update(self, bar_index: int) -> None:
        return None


def _wave(n: int) -> "dict[str, pd.Series]":
    opens = pd.Series([38_000.0 * (1.0 + 0.12 * math.sin(i / 4.0)) for i in range(n)])
    return {EMA_SERIES: opens.ewm(span=21, adjust=False).mean(), OPEN_SERIES: opens}


def _held_at(i: int) -> "tuple[str, ...]":
    # 保有の有無を足ごとに決定論で切り替え、「保有中は読まない」の経路を必ず通す。
    return ("sell",) if i % 5 == 0 else ()


def _drive(n: int, period: int):
    series = _wave(n)
    spy = _IndicatorSpy(series)
    strategy = EmaDeviationShort()
    strategy.on_init(_config(ma_period=period), spy)
    per_bar = []
    for i in range(n):
        before = len(spy.reads)
        strategy.on_new_bar(i, spy, _account(*_held_at(i)))
        per_bar.append(len(spy.reads) - before)
    return spy.reads, per_bar


def _declared_reads(n: int) -> "set[tuple[str, int]]":
    expected = set()
    for i in range(1, n):
        if not _held_at(i):
            expected |= {(EMA_SERIES, i - 1), (OPEN_SERIES, i)}
    return expected


@pytest.mark.parametrize(("n", "period"), [(40, 21), (400, 21), (400, 5)])
def test_every_read_is_used_by_a_decision_and_none_is_repeated(n: int, period: int) -> None:
    reads, _ = _drive(n, period)
    # 発行した読み − 判定に使う読み = 0（集合一致）かつ重複 0。
    assert set(reads) == _declared_reads(n)
    assert {k: c for k, c in Counter(reads).items() if c > 1} == {}


def test_reads_per_bar_do_not_grow_with_the_input_length_or_the_period() -> None:
    maxima = {(n, p): max(_drive(n, p)[1]) for n, p in [(40, 21), (400, 21), (400, 5)]}
    # 値そのものは固定せず、全条件で等しいことだけを表明する。
    assert len(set(maxima.values())) == 1, maxima
