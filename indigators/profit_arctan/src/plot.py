"""出力アダプタ: matplotlib による別ウィンドウ・ヒストグラム PNG 描画。

層名/責務:
    出力アダプタ。計算は arctan 成果物層に委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は ``indicator_separate_window`` の
    ``DRAW_HISTOGRAM``（DarkGreen, height 100）で描いたため、別ペインの棒ヒストグラムとして
    再現する。σ12 水準線（上下各 6 本）は水平点線で重ねる。

元 MQL4 の対応:
    ``#property indicator_separate_window`` + ``SetIndexStyle(0, DRAW_HISTOGRAM)`` +
    ``#property indicator_color1 DarkGreen``、レベル線（indicator_levelcolor C'84,84,84'）。

依存:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: arctan, core
"""

from __future__ import annotations


import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common_view import level_colors  # noqa: E402

from .arctan import LEVEL_COUNT_COLUMN, arctan_levels, build_arctan  # noqa: E402
from .core import DEFAULT_PERIOD, DEFAULT_WINDOW  # noqa: E402

# 元 #property indicator_color1 DarkGreen。
_COLOR = "#006400"
_LEVEL_COLOR = "#545454"   # 元 indicator_levelcolor C'84,84,84'

# σ12 水準線（上方 6 本 + 下方 6 本）。
_LEVEL_KEYS: tuple[str, ...] = (
    "up_067", "up_128", "up_165", "up_196", "up_258", "up_329",
    "dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329",
)


def plot_arctan(
    df: pd.DataFrame,
    out_path: str = "profit_arctan.png",
    *,
    period: int = DEFAULT_PERIOD,
    ma_method: int = 1,
    bar_width: float = 0.1,
    window: int | None = DEFAULT_WINDOW,
    title: str = "PRO!fit_Arctan",
) -> str:
    """Arctan ヒストグラムを別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: クランプ済みレベルカウントのヒストグラム（DarkGreen）＋
    σ12 水準線（点線, 上下各 6 本）。

    Args:
        df: OHLC DataFrame（open/high/low/close 必須）。
        out_path: 出力 PNG パス。
        period: MA 平滑期間（既定 6）。
        ma_method: 0=SMA/1=EMA/2=SMMA/3=LWMA（既定 1=EMA）。
        bar_width: iARCTAN の角度スケール（既定 0.1）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    bands = build_arctan(df, period=period, ma_method=ma_method, bar_width=bar_width, window=window)
    levels = arctan_levels(df, period=period, ma_method=ma_method, bar_width=bar_width, window=window)
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
               label="arctan level count")
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
