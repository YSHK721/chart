"""計算量 4（§7）: バーが確定せず走行 H / L も更新されないティックでの発行が 0。

実測（§9-8・survey-facts 2026-08-29）: 全月 2,302,070 tick の H/L 更新ティック率は
bid 7.8% / mid 13.0%。**ティックの 87〜92% は前進評価 0 回**でなければならない。
ここを取り違えると、出力は正しいまま 1 ティックあたりの仕事だけが膨らむ（ISSUE-450 と同型）。
"""
from __future__ import annotations

from dashboard_ui.domain.bar import Bar
from dashboard_ui.tests.complexity.conftest import ForwardSpy, Registry
from dashboard_ui.usecase.sheet_models import SheetInstance
from dashboard_ui.usecase.update_reach_sheet import refresh_projection

_INSTANCE = SheetInstance("ma_marod", "default", {"length": 24}, "1m",
                          intrabar_capable=True)


def _bar(**over) -> Bar:
    base = dict(time=1_700_000_000, open=100.0, high=110.0, low=90.0, close=100.0)
    base.update(over)
    return Bar(**base)


def _refresh(cache, bar, forward):
    return refresh_projection(cache, forming_bar=bar, instances=(_INSTANCE,),
                              dataset_ref="x", forward_port=forward,
                              registry=Registry({"ma_marod"}))


def test_a_tick_inside_the_running_range_issues_nothing() -> None:
    forward = ForwardSpy()
    cache = _refresh(None, _bar(), forward)
    forward.calls.clear()

    _refresh(cache, _bar(close=105.0), forward)

    assert forward.calls == []


def test_the_issue_count_does_not_grow_with_the_number_of_plain_ticks() -> None:
    """オーダーの表明（2 点固定）: ティック 50 本と 500 本で発行が変わらない。"""
    issued = {}
    for tick_count in (50, 500):
        forward = ForwardSpy()
        cache = _refresh(None, _bar(), forward)
        forward.calls.clear()

        for tick in range(tick_count):
            cache = _refresh(cache, _bar(close=95.0 + (tick % 100) * 0.1), forward)

        issued[tick_count] = len(forward.calls)

    assert issued[50] == issued[500] == 0


def test_only_the_ticks_that_move_the_running_extremes_issue_anything() -> None:
    """発行が起きる契機が「走行 H / L の更新」だけであることを固定する。"""
    forward = ForwardSpy()
    cache = _refresh(None, _bar(), forward)
    forward.calls.clear()

    for tick in range(100):
        cache = _refresh(cache, _bar(close=100.0 + tick * 0.01), forward)
    assert forward.calls == []

    _refresh(cache, _bar(high=120.0), forward)
    assert forward.calls != []
