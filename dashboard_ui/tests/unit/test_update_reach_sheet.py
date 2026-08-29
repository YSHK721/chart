"""UC-02（段 2・ティック）: epoch が不変なら前進評価を 1 回も発行しない。

§5.5.4 / §7 の実測: メビウス係数は**現在の価格 `C` に依存しない**（前バーの状態と走行 H / L
だけで決まる）。したがって再当てはめの契機は「バー確定」と「走行 H / L の更新」だけであり、
それ以外のティックでの発行は **0 回**でなければならない。
実測（§9-8）: H/L 更新ティック率は bid 7.8% / mid 13.0%。ティックの 87〜92% は発行 0 回。
"""
from __future__ import annotations

import pytest

from dashboard_ui.domain.bar import Bar, RunningExtreme
from dashboard_ui.usecase.sheet_models import SheetInstance
from dashboard_ui.usecase.update_reach_sheet import Epoch, refresh_projection


class FakeForward:
    """P-3 の Test Spy。計算量表明はこの面だけを数える。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def value_at_close(self, *, indicator_id, variant, params, dataset_ref,
                       timeframe, close):
        self.calls.append((indicator_id, timeframe, close))
        return (2.0 * close + 300.0) / (close + 200.0)


class FakeBreakpoints:
    def breakpoints(self, *, bar, params, prev_value):
        return (bar.low, bar.high)


class FakeRegistry:
    def __init__(self, ids: set[str]) -> None:
        self._ids = ids

    def resolve(self, indicator_id):
        return FakeBreakpoints() if indicator_id in self._ids else None

    def invertible_ids(self):
        return frozenset(self._ids)


_INSTANCE = SheetInstance("ma_marod", "default", {"length": 24}, "1m",
                          intrabar_capable=True)
_TICKVOL = SheetInstance("tickvol", "default", {}, "1m", intrabar_capable=True)


def _bar(time: int = 1_700_000_000, high: float = 110.0, low: float = 90.0,
         close: float = 100.0) -> Bar:
    return Bar(time=time, open=100.0, high=high, low=low, close=close)


def _refresh(cache, bar, *, forward, instances=(_INSTANCE,), ids=None):
    return refresh_projection(
        cache,
        forming_bar=bar,
        instances=instances,
        dataset_ref="jp225_tick",
        forward_port=forward,
        registry=FakeRegistry({"ma_marod"} if ids is None else ids),
    )


class TestEpoch:
    def test_the_epoch_is_the_bar_time_and_the_running_extremes(self) -> None:
        epoch = Epoch.of(_bar())

        assert epoch == Epoch(bar_time=1_700_000_000,
                              running=RunningExtreme(high=110.0, low=90.0))

    def test_the_close_does_not_take_part_in_the_epoch(self) -> None:
        """係数は `C` に依存しない。終値が動いただけで epoch を変えてはならない。"""
        assert Epoch.of(_bar(close=100.0)) == Epoch.of(_bar(close=105.0))


class TestNoWasteOnPlainTicks:
    def test_the_first_refresh_fits_the_maps(self) -> None:
        forward = FakeForward()

        cache = _refresh(None, _bar(), forward=forward)

        assert _INSTANCE.key in cache.maps
        assert forward.calls != []

    def test_a_tick_that_changes_nothing_issues_no_forward_evaluation(self) -> None:
        forward = FakeForward()
        cache = _refresh(None, _bar(), forward=forward)
        forward.calls.clear()

        refreshed = _refresh(cache, _bar(close=101.0), forward=forward)

        assert forward.calls == []
        assert refreshed is cache

    def test_many_plain_ticks_still_issue_nothing(self) -> None:
        """オーダーの表明: ティック数を増やしても発行は増えない。"""
        forward = FakeForward()
        cache = _refresh(None, _bar(), forward=forward)
        forward.calls.clear()

        for tick in range(200):
            cache = _refresh(cache, _bar(close=100.0 + tick * 0.01), forward=forward)

        assert forward.calls == []

    def test_the_cached_map_still_answers_without_any_forward_evaluation(self) -> None:
        """§5.5.4: 係数を決めた後の価格評価は前進評価を一切呼ばない。"""
        forward = FakeForward()
        cache = _refresh(None, _bar(), forward=forward)
        forward.calls.clear()

        values = [cache.maps[_INSTANCE.key].value_at(price)
                  for price in (95.0, 100.0, 105.0)]

        assert forward.calls == []
        assert all(value > 0 for value in values)


class TestRefitTriggers:
    def test_a_new_bar_refits(self) -> None:
        forward = FakeForward()
        cache = _refresh(None, _bar(), forward=forward)
        forward.calls.clear()

        refreshed = _refresh(cache, _bar(time=1_700_000_060), forward=forward)

        assert forward.calls != []
        assert refreshed is not cache

    def test_a_new_running_high_refits(self) -> None:
        forward = FakeForward()
        cache = _refresh(None, _bar(), forward=forward)
        forward.calls.clear()

        _refresh(cache, _bar(high=115.0), forward=forward)

        assert forward.calls != []

    def test_a_new_running_low_refits(self) -> None:
        forward = FakeForward()
        cache = _refresh(None, _bar(), forward=forward)
        forward.calls.clear()

        _refresh(cache, _bar(low=85.0), forward=forward)

        assert forward.calls != []


class TestScope:
    def test_an_indicator_without_a_breakpoint_source_gets_no_map(self) -> None:
        """§5.5.1: `tickvol` の除外は列挙で書かない。`breakpoints()` が無い形で現れる。"""
        forward = FakeForward()

        cache = _refresh(None, _bar(), forward=forward,
                         instances=(_INSTANCE, _TICKVOL))

        assert _INSTANCE.key in cache.maps
        assert _TICKVOL.key not in cache.maps

    def test_an_indicator_without_a_breakpoint_source_issues_nothing(self) -> None:
        forward = FakeForward()

        _refresh(None, _bar(), forward=forward, instances=(_TICKVOL,), ids=set())

        assert forward.calls == []

    def test_no_forming_bar_yields_an_empty_cache(self) -> None:
        forward = FakeForward()

        cache = refresh_projection(
            None, forming_bar=None, instances=(_INSTANCE,), dataset_ref="x",
            forward_port=forward, registry=FakeRegistry({"ma_marod"}))

        assert cache.maps == {}
        assert forward.calls == []

    def test_the_span_comes_from_the_running_range_of_the_forming_bar(self) -> None:
        """参照実装 probe_heatmap.py:189 と同一（`max(H0 - L0, 1.0)`）。"""
        forward = FakeForward()

        _refresh(None, _bar(high=110.0, low=90.0), forward=forward)
        probes = sorted(close for _, _, close in forward.calls)

        # 無限端の区分は走行幅の 4 倍だけ外へ伸ばす（90 - 4*20 = 10 が最外の端）。
        assert probes[0] == pytest.approx(10.0 + (90.0 - 10.0) * 0.15)
