"""出力アダプタ: lightweight-charts への水平ライン追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``horizontal_line`` を持つ
    オブジェクト（chart）をダックタイピングで受ける（ガイド §2/§6）。価格帯別ブルベア
    レシオは時系列ではなく価格軸の分布であるため、勢力の強い価格帯を**水平価格ライン**
    として価格チャートに重畳する（ブル=支持帯=緑, ベア=抵抗帯=赤）。

元 VBA の対応:
    resPRP の比率（ブル: OL/LH% / ベア: HC/HL%）を価格帯ごとに色分け表示する
    ``displayFormatSet.DF_PricerangePower`` を、価格チャート上の水平線として再表現。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: ratio, core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .core import DEFAULT_INTERVAL
from .ratio import build_bull_bear_profile

_BULL_COLOR = "rgba(46, 158, 91, 0.9)"   # 支持帯（ブル）
_BEAR_COLOR = "rgba(210, 67, 58, 0.9)"   # 抵抗帯（ベア）


@runtime_checkable
class _Chart(Protocol):
    def horizontal_line(self, price: float, **kwargs): ...


def add_price_range_power(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    interval: float = DEFAULT_INTERVAL,
    range_from: float | None = None,
    range_to: float | None = None,
    top_n: int = 5,
    bull_color: str = _BULL_COLOR,
    bear_color: str = _BEAR_COLOR,
    width: int = 2,
) -> list:
    """勢力の強い価格帯を chart に水平ライン（ブル=緑/ベア=赤）として追加する。

    各価格帯のブル勢力（OL/LH% 合計）と net_power（ブル-ベア）から、ブル優位の上位
    ``top_n`` 帯を緑、ベア優位の上位 ``top_n`` 帯を赤で水平線描画する。勢力が 0 の帯
    （比率がすべて NaN/0）は描画しない（EMPTY_VALUE → 非描画。ガイド §4.5）。

    Args:
        chart: ``horizontal_line(price, **kwargs)`` を持つオブジェクト（duck typing）。
        df: OHLC DataFrame。
        interval/range_from/range_to: build_price_range_power と同じ意味。
        top_n: ブル・ベアそれぞれで描画する上位帯数（既定 5）。
        bull_color/bear_color: 線色。
        width: 線幅。

    Returns:
        生成した水平ラインオブジェクトのリスト（ブル→ベアの順）。

    Raises:
        ValueError: top_n < 0 の場合。
    """
    if top_n < 0:
        raise ValueError(f"top_n は 0 以上である必要があります: {top_n}")

    prof = build_bull_bear_profile(
        df, interval=interval, range_from=range_from, range_to=range_to
    )
    bands = prof.index.to_numpy(dtype=np.float64)
    bull = prof["bull_power"].to_numpy(dtype=np.float64)
    bear = prof["bear_power"].to_numpy(dtype=np.float64)
    net = prof["net_power"].to_numpy(dtype=np.float64)

    # ブル優位（net>0）/ベア優位（net<0）の帯を勢力強度で降順抽出。
    bull_idx = [i for i in np.argsort(-net) if net[i] > 0 and bull[i] > 0][:top_n]
    bear_idx = [i for i in np.argsort(net) if net[i] < 0 and bear[i] > 0][:top_n]

    lines = []
    for i in bull_idx:
        lines.append(chart.horizontal_line(
            price=float(bands[i]), color=bull_color, width=width, style="solid",
            text=f"BULL {bull[i]:.2f}", axis_label_visible=False,
        ))
    for i in bear_idx:
        lines.append(chart.horizontal_line(
            price=float(bands[i]), color=bear_color, width=width, style="solid",
            text=f"BEAR {bear[i]:.2f}", axis_label_visible=False,
        ))
    return lines
