"""層名: 出力アダプタ（matplotlib による別ウィンドウ・ライン PNG 描画）。

責務:
    出力アダプタ。計算は成果物層（rsi）へ委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は ``indicator_separate_window`` の RSI 線
    （DRAW_LINE, clrLime）を [0,100] のペインに描いたため、別ペイン
    のオシレーター線 1 本として再現する。σ 水準線 7 本（±1/2/3σ ＝ 点線グレー、
    中央線 50 ＝ 実線）を重ねる。subwindow の y 範囲は元 indicator_minimum 0 〜
    indicator_maximum 100 に合わせる。RSI 線の凡例は元 ``IndicatorShortName`` の
    "RSI-{適用価格名} ({period})"（Apply で適用価格名が変わる）を再現する。
    具体描画ライブラリ（matplotlib）を core/成果物層へ侵入させない（依存内向き）。

元 MQL4 対応:
    ``#property indicator_separate_window`` + ``SetIndexStyle(0, DRAW_LINE)`` +
    ``indicator_color1 clrLime``（元 ExtMABuffer の EMA 平滑線は ma_period 削除に伴い
    非対応・承認 2026-08-02）、OnInit の ``IndicatorShortName`` switch（Apply→
    "RSI-Open/High/Low/Median/Typical/Weighted close/Close price (period)"）、
    σ 水準線（StDevA1..A6 ±1/2/3σ ＋ 中央線 50, indicator_levelcolor C'84,84,84' /
    indicator_levelstyle STYLE_SOLID）、``indicator_minimum 0`` / ``indicator_maximum 100``。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: rsi, core
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .core import DEFAULT_APPLY, DEFAULT_RSI_PERIOD
from .rsi import RSI_COLUMN, build_rsi, rsi_levels

_RSI_COLOR = "#00ff00"     # 元 indicator_color1 clrLime
_LEVEL_COLOR = "#545454"   # 元 indicator_levelcolor C'84,84,84'

# σ 水準線（±1/2/3σ は点線、中央線 50 は実線）。
_SIGMA_KEYS: tuple[str, ...] = ("p1", "p2", "p3", "m1", "m2", "m3")

# 元 indicator_minimum / indicator_maximum。
_Y_MIN = 0.0
_Y_MAX = 100.0

# 元 OnInit の switch(Apply) → IndicatorShortName 適用価格名（既定外は Close）。
_APPLY_NAME: dict[int, str] = {
    1: "Open price",
    2: "High price",
    3: "Low price",
    4: "Median price",
    5: "Typical price",
    6: "Weighted close price",
}


def rsi_short_name(apply: int, rsi_period: int) -> str:
    """元 ``IndicatorShortName`` "RSI-{適用価格名} ({period})" を再現する。

    Args:
        apply: 適用価格選択（既定外は Close price）。
        rsi_period: RSI 期間。

    Returns:
        例: ``apply=5, rsi_period=6`` -> ``"RSI-Typical price (6)"``。
    """
    name = _APPLY_NAME.get(apply, "Close price")
    return f"RSI-{name} ({rsi_period})"


def plot_rsi(
    df: pd.DataFrame,
    out_path: str = "profit_rsi.png",
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    apply: int = DEFAULT_APPLY,
    title: str = "PRO!fitRSI",
) -> str:
    """RSI 線を別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: RSI 線（Lime）＋ σ 水準線 7 本
    （±1/2/3σ は点線グレー、50 は実線）。下段 y 範囲は [0,100]。warm-up
    （i<rsi_period）は元 iRSI 既定どおり 0 で描画される（NaN 無し）。RSI 線の凡例は
    "RSI-{適用価格名} ({period})"（Apply 依存）。

    Args:
        df: OHLC DataFrame（open/high/low/close 必須・**volume 不要**）。
        out_path: 出力 PNG パス。
        rsi_period: RSI 期間（既定 6）。
        apply: 適用価格選択（既定 5 -> Typical price）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    built = build_rsi(df, rsi_period=rsi_period, apply=apply)
    levels = rsi_levels(df, rsi_period=rsi_period, apply=apply)
    rsi = built[RSI_COLUMN].to_numpy(dtype=np.float64)
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

    # 別ウィンドウ相当: RSI 線（DRAW_LINE, clrLime）。
    ax_ind.plot(x, rsi, color=_RSI_COLOR, linewidth=1.4,
                label=rsi_short_name(apply, rsi_period))
    # σ 水準線（±1/2/3σ は点線グレー）。
    for key in _SIGMA_KEYS:
        ax_ind.axhline(levels[key], color=_LEVEL_COLOR, linewidth=0.8,
                       linestyle=":", alpha=0.7)
    # 中央線 50（実線）。
    ax_ind.axhline(levels["mid50"], color=_LEVEL_COLOR, linewidth=1.0,
                   linestyle="-", alpha=0.8)
    # 別ウィンドウ y 範囲（元 indicator_minimum 0 〜 indicator_maximum 100）。
    ax_ind.set_ylim(_Y_MIN, _Y_MAX)
    ax_ind.set_ylabel("RSI")
    ax_ind.set_xlabel("bar index")
    ax_ind.legend(loc="upper left", fontsize=9)
    ax_ind.grid(True, axis="y", alpha=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
