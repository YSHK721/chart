"""計算量テスト共通の Test Spy と素材。

CLAUDE.md 絶対命令 §4.1: 測るのは**時間ではなく回数**。最小形は「発行した計算 − 出力に
使った計算 = 0」。**回数そのものを期待値に焼き込まない**（固定するのは無駄の不在であって
実装詳細ではない）。オーダーの表明は入力を変えた 2 点以上で行う。
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from dashboard_ui.domain.bar import Bar
from dashboard_ui.usecase.sheet_models import (
    OscillatorSpec,
    ReachSheetRequest,
    SeriesRole,
    SheetInstance,
)

NOW = 1_700_000_000


def bars(closes, *, step: int = 60):
    return tuple(
        Bar(time=NOW + index * step, open=close, high=close + 1.0,
            low=close - 1.0, close=close)
        for index, close in enumerate(closes)
    )


def points(values, *, step: int = 60):
    return tuple((NOW + index * step, float(value)) for index, value in enumerate(values))


class SeriesSpy:
    """P-1 の Test Spy。発行したキーを記録する（数えるのはこの面だけ）。"""

    def __init__(self, series_by_key=None) -> None:
        self._series_by_key = dict(series_by_key or {})
        self.issued: "list[tuple]" = []

    def add(self, instance: SheetInstance, series) -> None:
        self._series_by_key[instance.key] = series

    def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
        key = (indicator_id, variant,
               json.dumps(dict(params), sort_keys=True, ensure_ascii=False, default=str),
               timeframe)
        self.issued.append(key)
        return self._series_by_key.get(key, {})


class BarSpy:
    def __init__(self, bars_by_timeframe, *, forming: bool = False) -> None:
        self._bars_by_timeframe = dict(bars_by_timeframe)
        self._forming = bool(forming)
        self.requested: "list[str]" = []

    def bars(self, *, dataset_ref, timeframe):
        self.requested.append(timeframe)
        return self._bars_by_timeframe.get(timeframe, ())

    def forming_bar(self, *, dataset_ref, timeframe, now_unix):
        """形成中の足（既定は無し。`forming=True` のとき末尾の足を形成中として返す）。"""
        supplied = self._bars_by_timeframe.get(timeframe) or ()
        return supplied[-1] if (self._forming and supplied) else None


class ForwardSpy:
    """P-3 の Test Spy。前進評価はこの面からしか発行されない。"""

    def __init__(self) -> None:
        self.calls: "list[tuple]" = []

    def value_at_close(self, *, indicator_id, variant, params, dataset_ref,
                       timeframe, close):
        self.calls.append((indicator_id, timeframe, close))
        return (2.0 * close + 300.0) / (close + 200.0)


class Roles:
    """役割宣言（adapter 相当）。水準判定は実値の桁（現在値の 0.3〜3 倍）で行う。"""

    def __init__(self, specs=None) -> None:
        self._specs = dict(specs or {})

    def role_of(self, *, instance, series_name, values, reference_price):
        finite = [v for v in values if np.isfinite(v)]
        if not finite:
            return SeriesRole.NOT_LEVEL
        median = float(np.median(finite))
        inside = 0.3 * reference_price <= median <= 3.0 * reference_price
        return SeriesRole.PRICE_LEVEL if inside else SeriesRole.NOT_LEVEL

    def row_label(self, *, instance, series_name):
        # `|` 区切り（params_key は空白を含むため空白区切りにしない）。
        return f"{instance.indicator_id}|{series_name}|{instance.params_key}"

    def oscillator_spec(self, *, instance, series_names):
        return self._specs.get(instance.indicator_id)


class BreakpointStub:
    def breakpoints(self, *, bar, params, prev_value):
        return (bar.low, bar.high)

    def previous_value(self, *, bar, params):
        """上下分岐を持たない指標の面（P-4 と同じ面を持ち None を返す・LSP）。"""
        return None


class Registry:
    def __init__(self, ids) -> None:
        self._ids = set(ids)

    def resolve(self, indicator_id):
        return BreakpointStub() if indicator_id in self._ids else None

    def invertible_ids(self):
        return frozenset(self._ids)


def request_of(*instances, chart: str = "1m") -> ReachSheetRequest:
    return ReachSheetRequest(dataset_ref="jp225_tick", instances=instances,
                             chart_timeframe=chart)


def ma_instance(index: int, timeframe: str = "1m") -> SheetInstance:
    return SheetInstance("moving_averages", "default", {"length": index}, timeframe,
                         intrabar_capable=True)


def rsi_spec() -> OscillatorSpec:
    return OscillatorSpec(value_series="rsi", band_high_series="rsi_q90",
                          q_high=0.9, window_n=500, k_events=50)


@pytest.fixture
def series_spy() -> SeriesSpy:
    return SeriesSpy()
