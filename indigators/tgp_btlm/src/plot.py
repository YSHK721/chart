"""出力アダプタ: matplotlib による PNG 描画。

層名/責務:
    出力アダプタ。計算は build_btlm_bands に委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。レビュー用静止画・元 MT4 描画の再現に用いる。

元 MQL4 の対応:
    buf_mean(DRAW_LINE 実線) / buf_q1,buf_q2(DRAW_LINE + STYLE_DOT 点線)、
    いずれも色 MediumSlateBlue、chart_window(オーバーレイ)。

依存:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: bands, core
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .bands import build_btlm_bands
from .core import (
    DEFAULT_MAXBARS,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    BtlmFitter,
    mean_column,
    quantile_column,
)

# 元 MQL4 #property indicator_color1/2/3 = MediumSlateBlue。
_COLOR = "#7B68EE"


def plot_btlm(
    df: pd.DataFrame,
    fitter: BtlmFitter,
    out_path: str = "tgp_btlm.png",
    *,
    price: str = "open",
    maxbars: int = DEFAULT_MAXBARS,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    title: str = "!!R-tgp.BTLM-Ind",
) -> str:
    """価格にバンドを重ねた PNG を出力する。

    Args:
        df: 価格列を持つ DataFrame。
        fitter: BtlmFitter 実装。
        out_path: 出力 PNG パス。
        price/maxbars/q_low/q_high: build_btlm_bands と同じ意味。
        title: グラフタイトル。

    Returns:
        書き出した PNG のパス。
    """
    bands = build_btlm_bands(
        df, fitter, price=price, maxbars=maxbars, q_low=q_low, q_high=q_high
    )
    cols = {c.lower(): c for c in df.columns}
    price_series = df[cols[price.lower()]].to_numpy(dtype=np.float64)
    x = np.arange(len(df))

    mean = bands[mean_column()].to_numpy()
    lower = bands[quantile_column(q_low)].to_numpy()
    upper = bands[quantile_column(q_high)].to_numpy()

    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(x, price_series, color="#9e9e9e", linewidth=0.8, label=f"{price}")

    # NaN（窓外 = EMPTY_VALUE 相当）は自動的に途切れて描画されない。
    ax.plot(x, mean, color=_COLOR, linewidth=2.0, label="btlm mean")
    ax.plot(x, lower, color=_COLOR, linewidth=1.0, linestyle=":",
            label=f"q{int(round(q_low*100))}%")
    ax.plot(x, upper, color=_COLOR, linewidth=1.0, linestyle=":",
            label=f"q{int(round(q_high*100))}%")

    ax.set_title(title)
    ax.set_xlabel("bar index")
    ax.set_ylabel("price")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
