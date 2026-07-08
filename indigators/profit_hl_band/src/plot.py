"""層名: 出力アダプタ（matplotlib による overlay PNG 描画）。

責務:
    出力アダプタ。計算は成果物層（hl_band）へ委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。本指標は元 ``indicator_chart_window``（メインチャート
    重畳）であり separate ウィンドウ・ヒストグラムを持たない overlay 専用指標である。
    1 枚のメイン軸に、価格（close 等のライン）と価格軸の水平バンド線 8 本（上側 4 /
    下側 4）を描画する。separate ペインは無い。

    具体描画ライブラリ（matplotlib）を core/成果物層へ侵入させない（依存内向き）。

元 MQL4 対応:
    L16 indicator_color1 DarkGreen / L11 indicator_chart_window
        → メイン軸（overlay）。
    L220-227 StdDevArray[1..8] = iClose(1) ± iBandsOnArray(...)
        → up_*（緑系・加算）/ dn_*（減算）の水平線 8 本（axhline）。
    L249-252 OBJPROP_COLOR LimeGreen → バンド線色。
    元はプロット用バッファ（DRAW_HISTOGRAM 等）を持たないため separate ペインなし。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: hl_band
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .core import DEFAULT_WINDOW
from .hl_band import hl_band_levels

_BAND_COLOR = "#32CD32"   # 元 OBJPROP_COLOR LimeGreen（上下バンド線）
_CLOSE_COLOR = "#616161"  # 価格（close）参考ライン

# 上側 4 本（緑系・加算）/ 下側 4 本（減算）。元 StdDevArray[1..8]。
_UP_KEYS: tuple[str, ...] = ("up_067", "up_165", "up_196", "up_258")
_DN_KEYS: tuple[str, ...] = ("dn_067", "dn_165", "dn_196", "dn_258")


def plot_hl_band(
    df: pd.DataFrame,
    out_path: str = "profit_hl_band.png",
    *,
    title: str = "PRO!fit_HLBand",
    window: int | None = DEFAULT_WINDOW,
    normalize: bool = True,
) -> str:
    """価格 + 8 本の水平バンド線（上側 4 / 下側 4）をメイン軸に描画する（overlay）。

    本指標は separate ペインを持たない（元 chart_window）。価格（high/low 参考、
    close があればライン）に、close[-2] への ±band 投影 8 本を axhline で重畳する。

    Args:
        df: high/low/close を含む DataFrame（列名大小不問）。
        out_path: 出力 PNG パス。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。

    Raises:
        KeyError: high/low/close 列が無い場合。
        ValueError: N<2（close[-2] 不在）の場合（成果物層ガード）。
    """
    levels = hl_band_levels(df, window=window, normalize=normalize)

    cols = {c.lower(): c for c in df.columns}
    high = df[cols["high"]].to_numpy(dtype=np.float64)
    low = df[cols["low"]].to_numpy(dtype=np.float64)
    close = df[cols["close"]].to_numpy(dtype=np.float64)
    x = np.arange(len(df))

    fig, ax = plt.subplots(figsize=(14, 7))

    # --- 価格（参考）---
    ax.plot(x, high, color="#9e9e9e", linewidth=0.8, label="high")
    ax.plot(x, low, color="#bdbdbd", linewidth=0.8, label="low")
    ax.plot(x, close, color=_CLOSE_COLOR, linewidth=1.0, label="close")

    # --- 水平バンド線 8 本（上側 4 / 下側 4）。available=False 時は描画しない ---
    if levels.get("available", True):
        for key in (*_UP_KEYS, *_DN_KEYS):
            ax.axhline(
                levels[key], color=_BAND_COLOR, linewidth=0.9, linestyle="-", alpha=0.85
            )
    # 起点 close_ref（close[-2]）の参照線（点線・参考）。
    ax.axhline(
        levels["close_ref"], color="#888888", linewidth=0.8, linestyle="--", alpha=0.6
    )

    ax.set_title(title)
    ax.set_ylabel("price")
    ax.set_xlabel("bar index")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
