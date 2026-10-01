"""CalcProbe の計算量テスト（読んだ値 − 判定に使った値 = 0・入力長に依らない 1 足あたりの読み）。

観測の境界:
    戦略が指標を読む口は `IndicatorPort`（`on_new_bar` の引数）だけであり、これは宣言された
    境界である。Test Spy をその口へ渡し、系列の要素読み（`iloc[k]`）を (系列名, 位置) で数える。
    戦略の内部名は差し替えない。

期待値の導き方（回数をリテラルで焼き込まない）:
    売買規則が宣言する入力は「足 i で sma[i-1] を読み、それが成立していれば open[i] を読む」
    である。期待集合はこの宣言を足ごとに辿って作り、発行した読みと**集合として一致**し、
    かつ重複が 0（＝同じ値を 2 度読まない）であることを表明する。
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

import pandas as pd
import pytest

from simulator.adapter.strategy.calc_probe import CalcProbe

#: CalcProbe が読む系列名（束縛 `simulator/main/ea_bindings/calc_probe.py` が登録する名前）。
OPEN_SERIES = "open"
SMA_SERIES = "sma"
from simulator.domain.account import Account
from simulator.usecase.ports import IndicatorPort


class _IlocSpy:
    def __init__(self, name: str, series: pd.Series, reads: "list[tuple[str, int]]") -> None:
        self._name, self._series, self._reads = name, series, reads

    def __getitem__(self, k: int) -> Any:
        self._reads.append((self._name, int(k)))
        return self._series.iloc[k]


class _SeriesSpy:
    def __init__(self, name: str, series: pd.Series, reads: "list[tuple[str, int]]") -> None:
        self.iloc = _IlocSpy(name, series, reads)

    def __len__(self) -> int:
        return len(self.iloc._series)


class _IndicatorSpy(IndicatorPort):
    def __init__(self, series: "dict[str, pd.Series]") -> None:
        self.reads: "list[tuple[str, int]]" = []
        self._series = series

    def get(self, name: str) -> Any:
        return _SeriesSpy(name, self._series[name], self.reads)

    def update(self, bar_index: int) -> None:
        return None


def _series(n: int, period: int) -> "dict[str, pd.Series]":
    # 始値が SMA を上下に跨ぐ決定論的な並び。
    opens = pd.Series([100.0 + 5.0 * math.sin(i / 3.0) for i in range(n)])
    closes = opens + 0.5
    sma = closes.rolling(window=period, min_periods=period).mean()
    return {OPEN_SERIES: opens, SMA_SERIES: sma}


def _config() -> dict:
    return {"lot_size": 1.0, "volume_min": 0.1, "volume_max": 100.0, "volume_step": 0.1}


def _drive(n: int, period: int):
    """n 本ぶん on_new_bar を呼び、足ごとの読みと全体の読みを返す。"""
    series = _series(n, period)
    spy = _IndicatorSpy(series)
    strategy = CalcProbe()
    strategy.on_init(_config(), spy)
    account = Account(balance=1_000_000.0)
    per_bar = []
    for i in range(n):
        before = len(spy.reads)
        strategy.on_new_bar(i, spy, account)
        per_bar.append(len(spy.reads) - before)
    return series, spy.reads, per_bar


def _declared_reads(series: "dict[str, pd.Series]", n: int) -> "set[tuple[str, int]]":
    """売買規則が宣言する入力を足ごとに辿った期待集合。"""
    expected = set()
    for i in range(1, n):
        expected.add((SMA_SERIES, i - 1))
        if not math.isnan(series[SMA_SERIES].iloc[i - 1]):
            expected.add((OPEN_SERIES, i))
    return expected


@pytest.mark.parametrize(("n", "period"), [(40, 3), (400, 3), (400, 25)])
def test_every_read_is_used_by_a_decision_and_none_is_repeated(n: int, period: int) -> None:
    # Arrange / Act
    series, reads, _ = _drive(n, period)
    # Assert: 発行した読み − 判定に使う読み = 0（集合一致）かつ重複 0。
    assert set(reads) == _declared_reads(series, n)
    duplicates = {k: c for k, c in Counter(reads).items() if c > 1}
    assert duplicates == {}


def test_reads_per_bar_do_not_grow_with_the_input_length() -> None:
    """1 足あたりの読みの最大値が、入力長・窓長を変えても変わらない（O(1)/足）。"""
    # Arrange / Act
    maxima = {
        (n, period): max(_drive(n, period)[2]) for n, period in [(40, 3), (400, 3), (400, 25)]
    }
    # Assert: 値そのものは固定せず、全条件で等しいことだけを表明する。
    assert len(set(maxima.values())) == 1, maxima
