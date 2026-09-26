"""BarAlignedIndicators — 指標系列を run の Bar 列へ**時刻で**対応させる IndicatorPort（ISSUE-509）。

何を解くか:
    指標 registry はデータ実体の全行から作られる（期間より前の履歴で指標が温まる。MT5 原本
    `simulator/tests/fixtures/mt5/ma_slope_jp225_202501` の初回約定は期間 2 本目の足であり、
    期間前の履歴で EMA が温まっていることを示す）。一方 Bar 列は取得窓で絞られる。戦略は
    ``indicators.get(name).iloc[bar_index]`` で位置参照するため、窓のある run では
    ``bar_index=0`` が期間の先頭足を指すのに ``iloc[0]`` はデータ実体の先頭行を指していた
    （実測 2026-09-26・実 UI: 2026-09 の足で 2020-05 の値を読んでいた）。

どう解くか:
    各行の時刻（`row_times`）と Bar の時刻を突き合わせ、Bar 列が占める行の範囲
    ``[start, start + n)`` を求める。``get`` はその範囲を切り出した系列を返すので、
    ``iloc[bar_index]`` は「同じ時刻の行」を指す。範囲の前の行（温まりの履歴）は
    切り出した系列には入らないが、指標の**値**はその履歴を使って計算済みである。

Fail-Stop（対応を推測しない）:
    Bar の時刻が行に無い・行の並びで連続しない・系列の長さが行数と違う場合は `DataError`。
    ずれたまま走らせると数値は出るので状態検証では落ちない（本欠陥そのものの型）。

計算量:
    切り出しは系列名ごとに**1 回だけ**（初回の ``get`` で作り、以降は同じ実体を返す）。
    毎足の ``get`` で切り出すと足数 × 系列長の複製を作って捨てることになる。
    対応が恒等（窓の無い run）のときは切り出さず内側の系列をそのまま返す。
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from simulator.domain.bar_time import epoch_seconds
from simulator.domain.exceptions import DataError
from simulator.usecase.indicator_catalog_ports import IndicatorSeriesNamesPort
from simulator.usecase.ports import IndicatorPort


def bar_span(row_times: np.ndarray, bars: Sequence[Any]) -> "tuple[int, int]":
    """Bar 列が占める行の範囲 ``(start, 行数)`` を時刻で求める（連続でなければ `DataError`）。

    事前条件: ``row_times`` は行の並びのままの epoch 秒、``bars`` は非空。
    事後条件: ``row_times[start + i] == epoch_seconds(bars[i].time)`` が全 i で成り立つ。
    """
    bar_times = np.fromiter((epoch_seconds(b.time) for b in bars), dtype=np.int64, count=len(bars))
    start = int(np.searchsorted(row_times, bar_times[0]))
    end = start + len(bar_times)
    if end > len(row_times) or not np.array_equal(row_times[start:end], bar_times):
        raise DataError(
            "Bar 列が指標の行と時刻で連続に対応しません（指標と足を対応させられない）",
            context={
                "first_bar_time": int(bar_times[0]),
                "bars": len(bar_times),
                "rows": len(row_times),
                "start": start,
            },
        )
    return start, len(row_times)


class BarAlignedIndicators(IndicatorPort, IndicatorSeriesNamesPort):
    """内側 registry の系列を Bar 列の範囲へ切り出して返す IndicatorPort。"""

    def __init__(self, inner: Any, *, start: int, bars: int, rows: int) -> None:
        self._inner = inner
        self._start = start
        self._bars = bars
        self._rows = rows
        self._aligned: "dict[str, Any]" = {}

    def names(self) -> "tuple[str, ...]":
        return self._inner.names()

    def get(self, name: str) -> Any:
        aligned = self._aligned.get(name)
        if aligned is None:
            aligned = self._slice(name, self._inner.get(name))
            self._aligned[name] = aligned
        return aligned

    def update(self, bar_index: int) -> None:
        self._inner.update(bar_index)

    def _slice(self, name: str, series: Any) -> Any:
        if len(series) != self._rows:
            raise DataError(
                "指標系列の長さがデータ実体の行数と一致しません（時刻で対応させられない）",
                context={"name": name, "length": len(series), "rows": self._rows},
            )
        return series.iloc[self._start : self._start + self._bars].reset_index(drop=True)


def align_to_bars(inner: Any, row_times: np.ndarray, bars: Sequence[Any]) -> Any:
    """``inner`` を Bar 列へ時刻で対応させた IndicatorPort を返す（恒等なら ``inner`` のまま）。"""
    start, rows = bar_span(row_times, bars)
    if start == 0 and len(bars) == rows:
        return inner
    return BarAlignedIndicators(inner, start=start, bars=len(bars), rows=rows)
