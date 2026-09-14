"""層名: 出力アダプタ（matplotlib による別ウィンドウ・ライン PNG 描画）。

責務:
    出力アダプタ。計算は成果物層（stc）へ委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は ``indicator_separate_window`` の
    ``DRAW_LINE``（DarkGreen, width2）で描いたため、別ペインのオシレーター線として
    再現する。σ 水準線（P1/P2/M1/M2）は水平線（SOLID, グレー）で重ねる。subwindow の
    y 範囲は元 INDICATOR_MINIMUM=M2 〜 INDICATOR_MAXIMUM=P2（sub_min〜sub_max）に合わせる。
    具体描画ライブラリ（matplotlib）を core/成果物層へ侵入させない（依存内向き）。

元 MQL4 対応:
    ``#property indicator_separate_window`` + ``SetIndexStyle(0, DRAW_LINE)`` +
    ``indicator_color1 DarkGreen`` / ``indicator_width1 2``、σ 水準線
    （indicator_levelcolor C'84,84,84' / indicator_levelstyle STYLE_SOLID）、
    ``IndicatorSetDouble(INDICATOR_MINIMUM/MAXIMUM)``。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: stc, core
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .core import DEFAULT_PERIOD
from .stc import OSC_COLUMN, build_stc, stc_levels

_COLOR = "#006400"        # 元 indicator_color1 DarkGreen
_LEVEL_COLOR = "#545454"  # 元 indicator_levelcolor C'84,84,84'

# 重畳する σ 水準線（P1/P2/M1/M2）。
_LEVEL_KEYS: tuple[str, ...] = ("P1", "P2", "M1", "M2")


def plot_stc(
    df: pd.DataFrame,
    out_path: str = "profit_stc.png",
    *,
    period: int = DEFAULT_PERIOD,
    title: str = "PRO!fitSTC",
) -> str:
    """STC オシレーター線を別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: STC オシレーター線（DarkGreen, width2）＋
    σ 水準線 4 本（SOLID, グレー）。下段 y 範囲は sub_min(=M2)〜sub_max(=P2)。
    warm-up（i<period-1）は元 iStochastic 既定どおり 0 で描画される（NaN 無し）。

    Args:
        df: OHLC DataFrame（high/low/close 必須）。
        out_path: 出力 PNG パス。
        period: オシレーター期間（既定 70）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    bands = build_stc(df, period=period)
    levels = stc_levels(df, period=period)
    osc = bands[OSC_COLUMN].to_numpy(dtype=np.float64)
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

    # 別ウィンドウ相当: オシレーター線（DRAW_LINE, DarkGreen, width2）。
    ax_ind.plot(x, osc, color=_COLOR, linewidth=2.0, label=f"OSI ({period})")
    # σ 水準線（P1/P2/M1/M2, SOLID, グレー）。
    for key in _LEVEL_KEYS:
        ax_ind.axhline(levels[key], color=_LEVEL_COLOR, linewidth=0.8,
                       linestyle="-", alpha=0.7)
    # 別ウィンドウ y 範囲（元 INDICATOR_MINIMUM=M2 〜 INDICATOR_MAXIMUM=P2）。
    ax_ind.set_ylim(levels["sub_min"], levels["sub_max"])
    ax_ind.set_ylabel("oscillator")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
