"""§5.5.5 の背景色に要る「分位の目盛り」を、既存系列から組み立てる。

読み方は 1 つに固定される — **この価格で引けたら、各地平の分位はどこになるか**。そのため
仮定の指標値に当てる目盛り（:class:`~dashboard_ui.domain.continuous_quantile.QuantileScale`）
が要る。式そのもの（帯内の経験順位・帯外の GPD 接合・エピソード極値）は
:mod:`dashboard_ui.domain.continuous_quantile` が唯一所有しており、本モジュールは
**窓の取り方**（当該バーを観測に含めない因果境界）だけを揃える。

当てはめは呼び出し側から渡された当てはめキャッシュを通す。第 2 表のセルと同じキー・同じ窓
なので、同じ epoch では当てはめが 1 回で済む（§7: 分位を求めるたびに当てはめ直さない）。
"""
from __future__ import annotations

from typing import Mapping

import numpy as np

from dashboard_ui.domain.continuous_quantile import QuantileScale, excess_event_history
from dashboard_ui.usecase.sheet_models import OscillatorSpec


def quantile_scale_of(
    *,
    spec: OscillatorSpec,
    series: "Mapping[str, tuple[tuple[int, float], ...]]",
    tails,
    key: "tuple[str, str, str, str]",
) -> "QuantileScale | None":
    """1 instance ぶんの目盛り。素材が足りなければ None（無言で 0.5 を埋めない）。"""
    value_points = tuple(series.get(spec.value_series) or ())
    band_points = tuple(series.get(spec.band_high_series) or ())
    if len(value_points) < 2 or not band_points:
        return None

    times = [int(time) for time, _ in value_points]
    values = np.asarray([float(value) for _, value in value_points], dtype=np.float64)
    band_by_time = {int(time): float(value) for time, value in band_points}
    bands = np.asarray([band_by_time.get(time, np.nan) for time in times],
                       dtype=np.float64)
    # 因果境界: 当該バーは観測に含めない（当該バーの水準は当該バーより前だけで決まる）。
    events = excess_event_history(values[:-1], bands[:-1], excess=spec.excess)
    return QuantileScale(
        window_values=values[:-1][-int(spec.window_n):],
        band_high=float(bands[-1]),
        q_high=float(spec.q_high),
        tail=tails.tail_for(key, events, spec.k_events),
        excess=spec.excess,
    )
