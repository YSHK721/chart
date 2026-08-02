"""出力アダプタ: lightweight-charts への系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` を持つオブジェクト
    （chart）をダックタイピングで受ける（ガイド §2/§6）。専用ペインへ **1 本のバー** を描く。

系列名（F3 照合は catalog の SeriesDef 集合と突合）:
    - ``tickvol_updown`` : 直近 n 本の「上昇ティック数 − 下落ティック数」。ゼロを起点に、
      正なら上（緑）・負なら下（赤）へ伸びる。バーごとの色は payload の per-point ``color``
      （FakeChart の histogram 収集規約）で与える。

    2 本ではなく 1 本にする理由（依頼者指定 2026-08-02）: 上昇と下落の累積はほぼ同じ大きさで
    動くため（相関 0.9993〜0.9999）、2 本並べると鏡像になり読み取れない。差にすると非対称だけが
    残り、どちら側が優勢かが読める。

呼出規約:
    ``add_tickvol_updown(chart, df)``。API 経路（``adapter.compute.call_binding``）は df 以降を
    キーワード専用（kind="kw"）で渡す。

依存:
    標準: __future__, typing / 外部: numpy, pandas / 共有: common_view.lwc_adapter /
    プロジェクト内: .core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from common_view.lwc_adapter import SeriesLike  # noqa: E402
from common_view.lwc_adapter import resolve_times as _resolve_times  # noqa: E402

from .core import DEFAULT_WINDOW_N, NET_SERIES, net_updown

# 上昇優勢＝緑系 / 下落優勢＝赤系（ローソクの陽線・陰線と同じ向きの配色）。暗背景（#131722）前提。
_UP_COLOR = "rgba(38, 166, 154, 0.85)"
_DN_COLOR = "rgba(239, 83, 80, 0.85)"


_Series = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_histogram(self, name: str, **kwargs) -> _Series: ...


def add_tickvol_updown(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    time_column: "str | None" = None,
    up_color: str = _UP_COLOR,
    dn_color: str = _DN_COLOR,
) -> list:
    """chart に「上昇−下落ティック数の n 区間累積」を 1 本のバーとして追加する。

    Args:
        chart: ``create_histogram(name, **kwargs)`` を持つオブジェクト（duck typing）。
        df: OHLCV DataFrame（``up`` / ``dn`` 列＝方向内訳が必須。ティック由来データのみ）。
        window_n: 累積する本数（動的パラメータ）。当該バーを含む直近 N 本。
        time_column: 時刻列の明示指定（省略時は探索）。
        up_color / dn_color: 上昇優勢・下落優勢のバー色。

    Returns:
        追加した系列オブジェクトの list（要素 1）。

    Raises:
        KeyError: up / dn 列が無い、または時刻を解決できない場合。
        ValueError: ``window_n < 1`` の場合。
    """
    times = _resolve_times(df, time_column)
    values = np.asarray(net_updown(df, window_n=window_n), dtype=float)

    series = chart.create_histogram(NET_SERIES, color=up_color)
    frame = pd.DataFrame(
        {
            "time": times,
            NET_SERIES: values,
            # バーごとの色（符号で切り替える）。0 は上昇色に寄せる（下向きに描かないため）。
            "color": np.where(values < 0, dn_color, up_color),
        }
    ).dropna(subset=[NET_SERIES])
    series.set(frame)
    return [series]
