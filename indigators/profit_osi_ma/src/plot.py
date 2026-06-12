"""出力アダプタ: matplotlib による別ウィンドウ・ヒストグラム PNG 描画。

層名/責務:
    出力アダプタ。計算は osi_ma 層に委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は ``indicator_separate_window`` の
    ``DRAW_HISTOGRAM``（Red）で MAKairi を描いたため、別ペインの棒ヒストグラムとして
    再現する。水準線（``indicator_level1..4``: 1/0.5/-0.5/-1）は水平点線で重ねる。
    NaN（MA 未確定・最古バー）は ``bar()`` が自然に欠落させる。

元 MQL4 の対応:
    ``#property indicator_separate_window`` + ``SetIndexStyle(0, DRAW_HISTOGRAM)`` +
    ``#property indicator_color1 Red``、``#property indicator_level1..4``。

依存:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: osi_ma, core
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .core import DEFAULT_MA_MODE, DEFAULT_MA_PERIOD
from .osi_ma import KAIRI_COLUMN, build_osi_ma, osi_ma_levels

# 元 #property indicator_color1 Red。
_COLOR = "#d32f2f"
_LEVEL_COLOR = "#545454"  # 水準線（点線）


def plot_osi_ma(
    df: pd.DataFrame,
    out_path: str = "profit_osi_ma.png",
    *,
    ma_mode: int = DEFAULT_MA_MODE,
    ma_period: int = DEFAULT_MA_PERIOD,
    title: str = "PRO!fit_OSI_MA",
) -> str:
    """OSI_MA（MAKairi）ヒストグラムを別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: MAKairi ヒストグラム（Red）＋ 水準線（1/0.5/-0.5/-1）。
    NaN（最古バー・MA 未確定区間）は ``bar()`` が描画しない。

    Args:
        df: 少なくとも close 列を含む DataFrame（列名大小不問）。
        out_path: 出力 PNG パス。
        ma_mode: MA 種別（0=SMA,1=EMA,2=SMMA,3=LWMA, 既定 1）。
        ma_period: MA 期間（既定 21）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    bands = build_osi_ma(df, ma_mode=ma_mode, ma_period=ma_period)
    levels = osi_ma_levels()
    kairi = bands[KAIRI_COLUMN].to_numpy(dtype=np.float64)
    x = np.arange(len(df))

    cols = {c.lower(): c for c in df.columns}
    close = df[cols["close"]].to_numpy(dtype=np.float64)

    fig, (ax_price, ax_ind) = plt.subplots(
        2, 1, figsize=(14, 9), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )

    ax_price.plot(x, close, color="#9e9e9e", linewidth=0.9, label="close")
    ax_price.set_title(title)
    ax_price.set_ylabel("price")
    ax_price.legend(loc="upper left", fontsize=9)
    ax_price.grid(True, alpha=0.2)

    # 別ウィンドウ相当: MAKairi（符号付き＝0 基準の乖離率%）の棒ヒストグラム。
    ax_ind.axhline(0.0, color="#333333", linewidth=0.7)
    ax_ind.bar(x, kairi, width=0.8, color=_COLOR, alpha=0.85, label="MAKairi")
    # 水準線（元 indicator_level1..4: 1 / 0.5 / -0.5 / -1）を点線で重畳。
    for level in levels.values():
        ax_ind.axhline(level, color=_LEVEL_COLOR, linewidth=0.7,
                       linestyle=":", alpha=0.6)
    ax_ind.set_ylabel("kairi (%)")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
