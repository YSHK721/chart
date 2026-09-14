"""層名: 出力アダプタ（matplotlib による二系統 PNG 描画）。

責務:
    出力アダプタ。計算は成果物層（hlband）へ委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は二系統の描画を持つため両方を 1 枚の図で再現する:

    (A) 下段（separate window 相当）: hl_range のヒストグラム（clrLime）＋ σ 水準線
        4 本（avg/b165/b196/b258, SOLID, グレー C'84,84,84'）。y 範囲は sub_min(=0)〜
        sub_max(=b196*2)（元 INDICATOR_MINIMUM/MAXIMUM）。
    (B) 上段（main chart overlay 相当）: 価格（High/Low/Close 参考）＋ 最新 H/L へ投影
        した水平線 8 本（high_*/low_*, LimeGreen。元 OBJ_TREND 8 本）。

    具体描画ライブラリ（matplotlib）を core/成果物層へ侵入させない（依存内向き）。

元 MQL4 対応:
    L32 SetIndexStyle(0, DRAW_HISTOGRAM) / indicator_color1 clrLime → 下段ヒストグラム。
    L97-100 / indicator_levelcolor C'84,84,84' STYLE_SOLID            → 下段水平線 4 本。
    L102-103 INDICATOR_MINIMUM=0 / INDICATOR_MAXIMUM=b196*2           → 下段 y 範囲。
    L83-94 ObjectCreate(OBJ_TREND) 8 本 / OBJPROP_COLOR LimeGreen     → 上段水平線 8 本。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: hlband
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .hlband import (
    RANGE_COLUMN,
    build_hlband,
    hlband_levels,
    hlband_price_bands,
)

_HIST_COLOR = "#00FF00"     # 元 indicator_color1 clrLime
_LEVEL_COLOR = "#545454"    # 元 indicator_levelcolor C'84,84,84'
_OVERLAY_COLOR = "#32CD32"  # 元 OBJPROP_COLOR LimeGreen

_LEVEL_KEYS: tuple[str, ...] = ("avg", "b165", "b196", "b258")
_OVERLAY_KEYS: tuple[str, ...] = (
    "high_avg", "high_b165", "high_b196", "high_b258",
    "low_avg", "low_b165", "low_b196", "low_b258",
)


def plot_hlband(
    df: pd.DataFrame,
    out_path: str = "profit_hlband.png",
    *,
    title: str = "PRO!fitHLBand",
) -> str:
    """二系統（overlay + separate）を 1 枚の PNG に描画する。

    上段: High/Low/Close と最新 H/L 投影の水平線 8 本（LimeGreen）。
    下段: hl_range ヒストグラム（clrLime）＋ σ 水準線 4 本（SOLID, グレー）。
    下段 y 範囲は sub_min(=0)〜sub_max(=b196*2)。

    Args:
        df: high/low を含む DataFrame（close は参考表示・任意。列名大小不問）。
        out_path: 出力 PNG パス。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。

    Raises:
        KeyError: high/low 列が無い場合。
        ValueError: 空 DataFrame の場合（成果物層ガード）。
    """
    ranges = build_hlband(df)[RANGE_COLUMN].to_numpy(dtype=np.float64)
    levels = hlband_levels(df)
    bands = hlband_price_bands(df)

    cols = {c.lower(): c for c in df.columns}
    high = df[cols["high"]].to_numpy(dtype=np.float64)
    low = df[cols["low"]].to_numpy(dtype=np.float64)
    x = np.arange(len(df))

    fig, (ax_price, ax_ind) = plt.subplots(
        2, 1, figsize=(14, 9), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )

    # --- 上段: overlay（価格 + 最新 H/L 投影の水平線 8 本） ---
    ax_price.plot(x, high, color="#9e9e9e", linewidth=0.8, label="high")
    ax_price.plot(x, low, color="#bdbdbd", linewidth=0.8, label="low")
    if "close" in cols:
        ax_price.plot(
            x, df[cols["close"]].to_numpy(dtype=np.float64),
            color="#616161", linewidth=0.9, label="close",
        )
    for key in _OVERLAY_KEYS:
        ax_price.axhline(bands[key], color=_OVERLAY_COLOR, linewidth=0.9,
                         linestyle="-", alpha=0.8)
    ax_price.set_title(title)
    ax_price.set_ylabel("price")
    ax_price.legend(loc="upper left", fontsize=9)
    ax_price.grid(True, alpha=0.2)

    # --- 下段: separate（hl_range ヒストグラム + σ 水準線 4 本） ---
    ax_ind.bar(x, ranges, color=_HIST_COLOR, width=0.8, label=RANGE_COLUMN)
    for key in _LEVEL_KEYS:
        ax_ind.axhline(levels[key], color=_LEVEL_COLOR, linewidth=0.8,
                       linestyle="-", alpha=0.7)
    # 別ウィンドウ y 範囲（元 INDICATOR_MINIMUM=0 〜 INDICATOR_MAXIMUM=b196*2）。
    ax_ind.set_ylim(levels["sub_min"], levels["sub_max"])
    ax_ind.set_ylabel("hl_range")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
