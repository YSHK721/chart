"""出力アダプタ: matplotlib による別ウィンドウ・ヒストグラム PNG 描画。

層名/責務:
    出力アダプタ。計算は成果物層（rmm）に委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は ``indicator_separate_window`` の
    ``DRAW_HISTOGRAM``（clrLime, subwindow [-10,10]）で描いたため、別ペインの棒
    ヒストグラム 1 本として再現し、y 軸を [-10,10] に固定する。σ6 水準線
    （up_1s/up_2s/up_3s/dn_1s/dn_2s/dn_3s）は水平線（グレー）で重ねる。
    OBJ_RECTANGLE 背景色帯は描画関心＝対象外（SPEC §2）。

元 MQL4 対応:
    ``#property indicator_separate_window`` + ``indicator_minimum -10`` /
    ``indicator_maximum 10`` + ``DRAW_HISTOGRAM`` / ``indicator_color1 clrLime``、
    σ6 水準線（グレー C'84,84,84'）。

依存:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: rmm, core
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
from common import level_colors  # noqa: E402

from .core import DEFAULT_MA_PERIOD, DEFAULT_OSC_PERIOD  # noqa: E402
from .rmm import LEVEL_COUNT_COLUMN, build_rmm, rmm_levels  # noqa: E402

# 元 #property indicator_color1 clrLime はヒストグラムの per-bar 着色
# （common.level_colors）に置換済みのため、ベース色定数は持たない。
_LEVEL_COLOR = "#545454"   # 元 σ6 水準線 グレー C'84,84,84'
_LEVEL_KEYS = ("up_1s", "up_2s", "up_3s", "dn_1s", "dn_2s", "dn_3s")
# 元 indicator_minimum/maximum（subwindow の縦軸範囲）。
_PANE_MIN = -10.0
_PANE_MAX = 10.0


def plot_rmm(
    df: pd.DataFrame,
    out_path: str = "profit_rmm.png",
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    title: str = "PRO!fitRMM",
) -> str:
    """RMM レベルカウント・ヒストグラムを別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: レベルカウント・ヒストグラム 1 本（clrLime）＋
    σ6 水準線（グレー）。下段 y 軸は元 subwindow に合わせ [-10,10] 固定。

    Args:
        df: OHLCV DataFrame（high/low/close/volume 必須・列名大小不問）。
        out_path: 出力 PNG パス。
        osc_period: オシレーター期間（既定 6）。
        ma_period: EMA 期間（既定 6）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    bands = build_rmm(df, osc_period=osc_period, ma_period=ma_period)
    levels = rmm_levels(df, osc_period=osc_period, ma_period=ma_period)
    level_count = bands[LEVEL_COUNT_COLUMN].to_numpy(dtype=np.float64)
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

    # 別ウィンドウ相当: ヒストグラム 1 本（level_count は符号付き＝0 基準）。
    ax_ind.axhline(0.0, color="#333333", linewidth=0.7)
    ax_ind.bar(x, level_count, width=0.8, color=level_colors(level_count), alpha=0.85,
               label="rmm level count")
    # σ6 水準線（up 3 本 + dn 3 本）をグレー水平線で重畳。
    for key in _LEVEL_KEYS:
        ax_ind.axhline(levels[key], color=_LEVEL_COLOR, linewidth=0.7, alpha=0.7)
    ax_ind.set_ylim(_PANE_MIN, _PANE_MAX)  # 元 subwindow [-10,10]
    ax_ind.set_ylabel("level count")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
