"""出力アダプタ: matplotlib による別ウィンドウ描画 PNG。

層名/責務:
    出力アダプタ。計算は成果物層（oscillator2）に委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は ``indicator_separate_window`` の
    レベルカウント・ヒストグラム 1 本（ExtBufferLevelCount, DRAW_HISTOGRAM, DarkGreen）
    ＋ RCI 線 1 本（ExtBufferRCI, DRAW_LINE, clrLime）であるため、別ペインに棒ヒスト
    グラム 1 本と RCI 線 1 本を描く。σ6 水準線（up_165/up_196/up_258/dn_165/dn_196/
    dn_258）は水平線（グレー）で重ねる。subwindow の縦軸範囲は sub_min〜sub_max
    （LC クランプ無し・SPEC §7）。OBJ_RECTANGLE 背景色帯は描画関心＝対象外（SPEC §2）。

元 MQL4 対応:
    ``#property indicator_separate_window`` + ``DRAW_HISTOGRAM``（ExtBufferLevelCount,
    indicator_color1 DarkGreen, indicator_width1 2）+ ``DRAW_LINE``（ExtBufferRCI,
    indicator_color2 clrLime）+ σ6 水準線（グレー C'84,84,84'）。

依存:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: oscillator2
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root → common
from common_view import level_colors  # noqa: E402

from .oscillator2 import (  # noqa: E402
    LEVEL_COUNT_COLUMN,
    RCI_COLUMN,
    build_oscillator2,
    oscillator2_levels,
)

_HIST_COLOR = "#006400"   # 元 indicator_color1 DarkGreen（レベルカウント）
_RCI_COLOR = "#00ff00"    # 元 indicator_color2 clrLime（RCI 線）
_LEVEL_COLOR = "#545454"  # 元 σ6 水準線 グレー C'84,84,84'
_LEVEL_KEYS = ("up_165", "up_196", "up_258", "dn_165", "dn_196", "dn_258")


def plot_oscillator2(
    df: pd.DataFrame,
    out_path: str = "profit_oscillator2.png",
    *,
    osc_period: int = 6,
    stc_slow: int = 6,
    ma_period: int = 60,
    rci_period: int = 12,
    direction: bool = False,
    title: str = "PRO!fitOscillator",
) -> str:
    """レベルカウント・ヒストグラム＋RCI 線を別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: レベルカウント・ヒストグラム 1 本（DarkGreen）＋
    RCI 線 1 本（clrLime）＋ σ6 水準線（グレー）。下段 y 軸は元 subwindow に合わせ
    sub_min〜sub_max（LC クランプ無し）。

    Args:
        df: OHLCV DataFrame（open/high/low/close/volume 必須・列名大小不問）。
        out_path: 出力 PNG パス。
        osc_period: オシレーター期間（既定 6）。
        stc_slow: iStochastic slowing ＝ D 期間（既定 6）。
        ma_period: MAROD の EMA 期間（既定 60）。
        rci_period: RCI 期間（既定 12）。
        direction: RCI ソート方向（既定 False）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    built = build_oscillator2(
        df, osc_period=osc_period, stc_slow=stc_slow, ma_period=ma_period,
        rci_period=rci_period, direction=direction,
    )
    levels = oscillator2_levels(
        df, osc_period=osc_period, stc_slow=stc_slow, ma_period=ma_period,
        rci_period=rci_period, direction=direction,
    )
    lc = built[LEVEL_COUNT_COLUMN].to_numpy(dtype=np.float64)
    rci = built[RCI_COLUMN].to_numpy(dtype=np.float64)
    x = np.arange(len(df))

    cols = {str(c).lower(): c for c in df.columns}
    close = df[cols["close"]].to_numpy(dtype=np.float64)

    fig, (ax_price, ax_ind) = plt.subplots(
        2, 1, figsize=(14, 9), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )

    ax_price.plot(x, close, color="#9e9e9e", linewidth=0.9, label="close")
    ax_price.set_title(title)
    ax_price.set_ylabel("price")
    ax_price.legend(loc="upper left", fontsize=9)
    ax_price.grid(True, alpha=0.2)

    # 別ウィンドウ相当: ヒストグラム 1 本（符号付き＝0 基準）＋ RCI 線 1 本。
    ax_ind.axhline(0.0, color="#333333", linewidth=0.7)
    ax_ind.bar(x, lc, width=0.8, color=level_colors(lc), alpha=0.85,
               label="oscillator2 level count")
    ax_ind.plot(x, rci, color=_RCI_COLOR, linewidth=1.0, label="RCI")
    # σ6 水準線（up 3 本 + dn 3 本）をグレー水平線で重畳。
    for key in _LEVEL_KEYS:
        ax_ind.axhline(levels[key], color=_LEVEL_COLOR, linewidth=0.7, alpha=0.7)
    # 元 subwindow 範囲 sub_min〜sub_max（LC クランプ無し）。
    ax_ind.set_ylim(levels["sub_min"], levels["sub_max"])
    ax_ind.set_ylabel("level count / RCI")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
