"""層名: 出力アダプタ（matplotlib による別ウィンドウ・MACD 型 PNG 描画）。

責務:
    出力アダプタ。計算は成果物層（rsimacd）へ委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL は ``indicator_separate_window`` に
    MacdHistogram（DRAW_HISTOGRAM, C'133,219,24'）＋ RSIMACD 線（DRAW_LINE,
    C'205,232,65'）＋ Signal 線（DRAW_LINE, C'167,197,32'）を描いた MACD 型指標で
    あるため、別ペインにヒストグラム（bar）＋線 2 本として再現する。σ 水準線 7 本
    （±1/2/3σ ＝ 点線グレー、中央線 50 ＝ 実線）を重ねる。元指標は indicator_minimum
    /maximum を指定しない（[0,100] 制約なし）ため、本ペインは自動スケールとする。
    RSIMACD は価格 Typical 起点（iRSI）のため出来高（volume）は不要＝OHLC で成立する。
    具体描画ライブラリ（matplotlib）を core/成果物層へ侵入させない（依存内向き）。

元 MQL 対応:
    ``#property indicator_separate_window`` + ``SetIndexStyle(0,DRAW_HISTOGRAM)``
    （MacdHistogram, indicator_color1 C'133,219,24'）+ ``SetIndexStyle(1,DRAW_LINE)``
    （Macd, label "RSIMACD", indicator_color2 C'205,232,65'）+
    ``SetIndexStyle(2,DRAW_LINE)``（Signal, label "Signal", indicator_color3
    C'167,197,32'）、σ 水準線（StDevA1..A6 ±1/2/3σ ＋ 中央線 50,
    indicator_levelcolor C'84,84,84' / indicator_levelstyle STYLE_SOLID）。
    元指標は indicator_minimum/maximum を指定しない（自動スケール）。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: rsimacd, core
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_RSI_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
)
from .rsimacd import (
    HIST_COLUMN,
    MACD_COLUMN,
    SIGNAL_COLUMN,
    build_rsimacd,
    rsimacd_levels,
)

_HIST_COLOR = "#85db18"    # 元 indicator_color1 C'133,219,24'
_MACD_COLOR = "#cde841"    # 元 indicator_color2 C'205,232,65'
_SIGNAL_COLOR = "#a7c520"  # 元 indicator_color3 C'167,197,32'
_LEVEL_COLOR = "#545454"   # 元 indicator_levelcolor C'84,84,84'

# σ 水準線（±1/2/3σ は点線、中央線 50 は実線）。
_SIGMA_KEYS: tuple[str, ...] = ("p1", "p2", "p3", "m1", "m2", "m3")


def plot_rsimacd(
    df: pd.DataFrame,
    out_path: str = "profit_rsi_macd.png",
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
    title: str = "PRO!fitRSIMACD",
) -> str:
    """RSIMACD ヒストグラム・RSIMACD/Signal 線を別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: ヒストグラム（bar, 黄緑）＋ RSIMACD 線 ＋ Signal 線
    ＋ σ 水準線 7 本（±1/2/3σ は点線グレー、50 は実線）。下段は [0,100] 制約なしの
    自動スケール（元指標は indicator_minimum/maximum 未指定）。warm-up
    （i<rsi_period）は元 iRSI 既定どおり 0 起点で描画される（NaN 無し）。

    Args:
        df: OHLC DataFrame（high/low/close 必須・**volume 不要**）。
        out_path: 出力 PNG パス。
        rsi_period: RSI 期間（既定 13）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    built = build_rsimacd(
        df, rsi_period=rsi_period, fast=fast, slow=slow, signal=signal,
    )
    levels = rsimacd_levels(
        df, rsi_period=rsi_period, fast=fast, slow=slow, signal=signal,
    )
    hist = built[HIST_COLUMN].to_numpy(dtype=np.float64)
    macd = built[MACD_COLUMN].to_numpy(dtype=np.float64)
    sig = built[SIGNAL_COLUMN].to_numpy(dtype=np.float64)
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

    # 別ウィンドウ相当: ヒストグラム（DRAW_HISTOGRAM）＋ RSIMACD/Signal 線（DRAW_LINE）。
    ax_ind.bar(x, hist, color=_HIST_COLOR, width=0.8, label="MacdHistogram")
    ax_ind.plot(x, macd, color=_MACD_COLOR, linewidth=1.4, label="RSIMACD")
    ax_ind.plot(x, sig, color=_SIGNAL_COLOR, linewidth=1.2, label="Signal")
    # σ 水準線（±1/2/3σ は点線グレー）。
    for key in _SIGMA_KEYS:
        ax_ind.axhline(levels[key], color=_LEVEL_COLOR, linewidth=0.8,
                       linestyle=":", alpha=0.7)
    # 中央線 50（実線）。
    ax_ind.axhline(levels["mid50"], color=_LEVEL_COLOR, linewidth=1.0,
                   linestyle="-", alpha=0.8)
    # 元指標は indicator_minimum/maximum 未指定のため自動スケール（[0,100] 制約なし）。
    ax_ind.set_ylabel("RSIMACD")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
