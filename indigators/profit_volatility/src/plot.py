"""出力アダプタ: matplotlib による別ウィンドウ・ヒストグラム PNG 描画。

層名/責務:
    出力アダプタ。計算は volatility 成果物層に委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 ``PRO!fit_Volatility`` は
    ``indicator_separate_window`` の ``DRAW_HISTOGRAM``（DarkGreen, height 100）で
    描いたため、別ペインの棒ヒストグラムとして再現する。σ12 水準線（上下各 6 本）は
    水平点線で重ねる。

元 MQL4 の対応:
    ``#property indicator_separate_window`` + ``#property indicator_height 100`` +
    ``SetIndexStyle(0, DRAW_HISTOGRAM)`` + ``#property indicator_color1 DarkGreen``、
    レベル線（``indicator_levelcolor C'84,84,84'``）。

依存:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: src.volatility, src.core
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

from .core import DEFAULT_PERIOD  # noqa: E402
from .volatility import (  # noqa: E402
    LEVEL_COUNT_COLUMN,
    build_volatility,
    volatility_levels,
)

# 元 #property indicator_color1 DarkGreen。
_COLOR = "#006400"
_LEVEL_COLOR = "#545454"   # 元 indicator_levelcolor C'84,84,84'

# σ12 水準線（上方 6 本 + 下方 6 本）。
_LEVEL_KEYS: tuple[str, ...] = (
    "up_067", "up_128", "up_165", "up_196", "up_258", "up_329",
    "dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329",
)


def plot_volatility(
    df: pd.DataFrame,
    out_path: str = "profit_volatility.png",
    *,
    period: int = DEFAULT_PERIOD,
    title: str = "PRO!fit_Volatility",
) -> str:
    """Volatility ヒストグラムを別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: クランプ済みレベルカウントのヒストグラム（DarkGreen）＋
    σ12 水準線（点線, 上下各 6 本）。

    Args:
        df: OHLC DataFrame（open/high/low/close 必須）。
        out_path: 出力 PNG パス。
        period: 乖離をとる足数（既定 6）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    bands = build_volatility(df, period=period)
    levels = volatility_levels(df, period=period)
    lc = bands[LEVEL_COUNT_COLUMN].to_numpy(dtype=np.float64)
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

    # 別ウィンドウ相当: ヒストグラム（レベルカウントは符号付き＝0 基準の温度）。
    ax_ind.axhline(0.0, color="#333333", linewidth=0.7)
    ax_ind.bar(x, lc, width=0.8, color=level_colors(lc), alpha=0.85,
               label="volatility level count")
    # σ12 水準線（上下を点線で重畳）。
    for sigma_key in _LEVEL_KEYS:
        ax_ind.axhline(levels[sigma_key], color=_LEVEL_COLOR, linewidth=0.7,
                       linestyle=":", alpha=0.6)
    ax_ind.set_ylabel("level count")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
