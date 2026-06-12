"""層名: 出力アダプタ（matplotlib による別ウィンドウ・ライン PNG 描画）。

責務:
    出力アダプタ。計算は成果物層（mfi）へ委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は ``indicator_separate_window`` の MFI 線
    ＋ EMA 平滑線（共に DRAW_LINE, clrLime）を [0,100] のペインに描いたため、別ペイン
    のオシレーター線 2 本として再現する。σ 水準線 7 本（±1/2/3σ ＝ 点線グレー、
    中央線 50 ＝ 実線）を重ねる。subwindow の y 範囲は元 indicator_minimum 0 〜
    indicator_maximum 100 に合わせる。具体描画ライブラリ（matplotlib）を core/成果物層
    へ侵入させない（依存内向き）。

元 MQL4 対応:
    ``#property indicator_separate_window`` + ``SetIndexStyle(0/1, DRAW_LINE)`` +
    ``indicator_color1/2 clrLime``、σ 水準線（StDevA1..A6 ±1/2/3σ ＋ 中央線 50,
    indicator_levelcolor C'84,84,84' / indicator_levelstyle STYLE_SOLID）、
    ``indicator_minimum 0`` / ``indicator_maximum 100``。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: mfi, core
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .core import DEFAULT_MA_PERIOD, DEFAULT_MFI_PERIOD
from .mfi import MA_COLUMN, MFI_COLUMN, build_mfi, mfi_levels

_MFI_COLOR = "#00ff00"     # 元 indicator_color1 clrLime
_MA_COLOR = "#1b8f1b"      # 元 indicator_color2 clrLime（識別性のため濃緑で重畳）
_LEVEL_COLOR = "#545454"   # 元 indicator_levelcolor C'84,84,84'

# σ 水準線（±1/2/3σ は点線、中央線 50 は実線）。
_SIGMA_KEYS: tuple[str, ...] = ("p1", "p2", "p3", "m1", "m2", "m3")

# 元 indicator_minimum / indicator_maximum。
_Y_MIN = 0.0
_Y_MAX = 100.0


def plot_mfi(
    df: pd.DataFrame,
    out_path: str = "profit_mfi.png",
    *,
    mfi_period: int = DEFAULT_MFI_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    title: str = "PRO!fitMFI",
) -> str:
    """MFI 線・EMA 平滑線を別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: MFI 線（Lime）＋ EMA 平滑線 ＋ σ 水準線 7 本
    （±1/2/3σ は点線グレー、50 は実線）。下段 y 範囲は [0,100]。warm-up
    （i<mfi_period）は元 iMFI 既定どおり 0 で描画される（NaN 無し）。

    Args:
        df: OHLCV DataFrame（high/low/close/volume 必須）。
        out_path: 出力 PNG パス。
        mfi_period: MFI 期間（既定 14）。
        ma_period: EMA 期間（既定 5）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    built = build_mfi(df, mfi_period=mfi_period, ma_period=ma_period)
    levels = mfi_levels(df, mfi_period=mfi_period, ma_period=ma_period)
    mfi = built[MFI_COLUMN].to_numpy(dtype=np.float64)
    ma = built[MA_COLUMN].to_numpy(dtype=np.float64)
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

    # 別ウィンドウ相当: MFI 線・EMA 平滑線（DRAW_LINE, clrLime）。
    ax_ind.plot(x, mfi, color=_MFI_COLOR, linewidth=1.4,
                label=f"MFI ({mfi_period})")
    ax_ind.plot(x, ma, color=_MA_COLOR, linewidth=1.2,
                label=f"MFI MA ({ma_period})")
    # σ 水準線（±1/2/3σ は点線グレー）。
    for key in _SIGMA_KEYS:
        ax_ind.axhline(levels[key], color=_LEVEL_COLOR, linewidth=0.8,
                       linestyle=":", alpha=0.7)
    # 中央線 50（実線）。
    ax_ind.axhline(levels["mid50"], color=_LEVEL_COLOR, linewidth=1.0,
                   linestyle="-", alpha=0.8)
    # 別ウィンドウ y 範囲（元 indicator_minimum 0 〜 indicator_maximum 100）。
    ax_ind.set_ylim(_Y_MIN, _Y_MAX)
    ax_ind.set_ylabel("MFI")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
