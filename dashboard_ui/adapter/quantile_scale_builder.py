"""§5.5.5 の背景色に要る「分位の目盛り」を、既存系列から組み立てる。

読み方は 1 つに固定される — **この価格で引けたら、各地平の分位はどこになるか**。そのため
仮定の指標値に当てる目盛り（:class:`~dashboard_ui.domain.continuous_quantile.QuantileScale`）
が要る。式そのもの（帯内の経験順位・帯外の GPD 接合・エピソード極値）は
:mod:`dashboard_ui.domain.continuous_quantile` が唯一所有する。突き合わせと因果境界
（当該バーを観測に含めない）も同モジュールの :class:`BandObservations` が所有しており、
本モジュールは**宣言（spec）からどの系列を引くか**と**窓の本数**を与えるだけである。

当てはめは呼び出し側から渡された当てはめキャッシュを通す。第 2 表のセルと同じキー・同じ窓
なので、同じ epoch では当てはめが 1 回で済む（§7: 分位を求めるたびに当てはめ直さない）。
"""
from __future__ import annotations

from typing import Mapping

from dashboard_ui.domain.continuous_quantile import (
    BandObservations,
    QuantileScale,
    excess_event_history,
)
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

    # 突き合わせと因果境界は domain の観測が唯一の所有者（第 2 表のセルと同じ観測を使う）。
    observed = BandObservations.of(value_points, band_points)
    history_values, history_bands = observed.history
    events = excess_event_history(history_values, history_bands, excess=spec.excess)
    return QuantileScale(
        window_values=history_values[-int(spec.window_n):],
        band_high=float(observed.bands[-1]),
        q_high=float(spec.q_high),
        tail=tails.tail_for(key, events, spec.k_events),
        excess=spec.excess,
    )
