"""計算量 5（§7 §5.3 固有）: GPD の再当てはめはイベント確定のときだけ。

§7 の表明そのもの:
    - `p` は既存系列から導く量であり、**新規の compute を発行してはならない**。
    - **GPD の再当てはめはイベント確定のときだけ**。エピソードが閉じないバーでの当てはめ回数が 0
      （`p` を求めるたびに当てはめ直さない）。
    - オーダーの表明: 表示するセル数を 33 → 66 にしても、当てはめ回数が**変わらない**。

回数そのものは期待値に焼き込まない。
"""
from __future__ import annotations

import numpy as np
import pytest

from dashboard_ui.domain import continuous_quantile as _cq
from dashboard_ui.tests.complexity.conftest import (
    BarSpy,
    Roles,
    SeriesSpy,
    bars,
    ma_instance,
    points,
    request_of,
    rsi_spec,
)
from dashboard_ui.usecase.build_reach_sheet import TailFitCache, build_reach_sheet
from dashboard_ui.usecase.sheet_models import SheetInstance


class FitSpy:
    """`fit_tail` の Test Spy（当てはめの発行はこの面からしか起きない）。"""

    def __init__(self, monkeypatch) -> None:
        self.calls = 0
        original = _cq.fit_tail

        def counted(events, *, k_events):
            self.calls += 1
            return original(events, k_events=k_events)

        monkeypatch.setattr(_cq, "fit_tail", counted)


@pytest.fixture
def fit_spy(monkeypatch) -> FitSpy:
    return FitSpy(monkeypatch)


_OSC = SheetInstance("profit_rsi", "default", {}, "1m", intrabar_capable=True)


def _series(values, bands):
    return {"rsi": points(values), "rsi_q90": points(bands)}


def _sheet(instances, spy, *, cache=None, extra_rows=0):
    timeframes = {instance.timeframe for instance in instances} | {"1m"}
    bar_spy = BarSpy({tf: bars([100.0] * 8) for tf in timeframes})
    return build_reach_sheet(
        request_of(*instances), series_port=spy, bar_port=bar_spy,
        roles=Roles({"profit_rsi": rsi_spec()}), tail_fit_cache=cache)


def test_no_refit_while_the_episode_is_still_open(fit_spy: FitSpy) -> None:
    """エピソードが閉じていないバーが増えても、当てはめは増えない。"""
    cache = TailFitCache()
    counts = []
    for extra_open_bars in (1, 5):
        spy = SeriesSpy()
        # 末尾は帯超が続く（＝エピソード未確定）。確定観測の数は変わらない。
        values = [10.0, 95.0, 10.0] + [95.0] * extra_open_bars
        spy.add(_OSC, _series(values, [90.0] * len(values)))
        fit_spy.calls = 0

        _sheet([_OSC], spy, cache=cache)
        counts.append(fit_spy.calls)

    assert counts[1] == 0


def test_repeating_the_build_without_new_events_refits_nothing(fit_spy: FitSpy) -> None:
    """`p` を求めるたびに当てはめ直さない（同じ確定観測列なら再当てはめ 0）。"""
    cache = TailFitCache()
    spy = SeriesSpy()
    values = [10.0] * 3 + [95.0, 10.0] * 20
    spy.add(_OSC, _series(values, [90.0] * len(values)))

    _sheet([_OSC], spy, cache=cache)
    after_first = fit_spy.calls
    for _ in range(20):
        _sheet([_OSC], spy, cache=cache)

    assert fit_spy.calls == after_first


def test_a_newly_closed_episode_does_refit(fit_spy: FitSpy) -> None:
    """規則が「二度と当てはめない」に退化していないこと（キャッシュの自己検査）。"""
    cache = TailFitCache()
    values = [10.0] * 3 + [95.0, 10.0] * 20
    spy = SeriesSpy()
    spy.add(_OSC, _series(values, [90.0] * len(values)))
    _sheet([_OSC], spy, cache=cache)
    before = fit_spy.calls

    grown = values + [96.0, 10.0]
    spy.add(_OSC, _series(grown, [90.0] * len(grown)))
    _sheet([_OSC], spy, cache=cache)

    assert fit_spy.calls > before


def test_doubling_the_rendered_cells_does_not_change_the_fit_count(
    fit_spy: FitSpy,
) -> None:
    """オーダーの表明（2 点固定）: 同じ instance の表示先が増えても当てはめは増えない。"""
    counts = {}
    for consumer_count in (1, 2):
        cache = TailFitCache()
        spy = SeriesSpy()
        values = [10.0] * 3 + [95.0, 10.0] * 20
        spy.add(_OSC, _series(values, [90.0] * len(values)))
        fit_spy.calls = 0

        for _ in range(consumer_count):
            _sheet([_OSC], spy, cache=cache)

        counts[consumer_count] = fit_spy.calls

    assert counts[1] == counts[2]


def test_the_number_of_ladder_rows_does_not_change_the_fit_count(
    fit_spy: FitSpy,
) -> None:
    """オーダーの表明（2 点固定）: ラダー行が増えても当てはめは増えない。"""
    counts = {}
    for row_count in (11, 23):
        cache = TailFitCache()
        spy = SeriesSpy()
        values = [10.0] * 3 + [95.0, 10.0] * 20
        spy.add(_OSC, _series(values, [90.0] * len(values)))
        rows = [ma_instance(index) for index in range(1, row_count + 1)]
        for index, instance in enumerate(rows):
            spy.add(instance, {"MA": points([100.0 + index] * 46)})
        fit_spy.calls = 0

        _sheet([_OSC, *rows], spy, cache=cache)
        counts[row_count] = fit_spy.calls

    assert counts[11] == counts[23]


def test_the_sheet_issues_no_compute_beyond_the_series_it_reads() -> None:
    """`p` は既存系列から導く量であり、新規の計算を発行しない。"""
    spy = SeriesSpy()
    values = [10.0] * 3 + [95.0, 10.0] * 20
    spy.add(_OSC, _series(values, [90.0] * len(values)))

    _sheet([_OSC], spy, cache=TailFitCache())

    assert spy.issued == [_OSC.key]
