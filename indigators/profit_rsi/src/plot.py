"""層名: 出力アダプタ（matplotlib による別ウィンドウ・ライン PNG 描画）。

責務:
    出力アダプタ。計算は成果物層（rsi）へ委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 MQL4 は ``indicator_separate_window`` の RSI 線
    （DRAW_LINE, clrLime）を [0,100] のペインに描いたため、別ペイン
    のオシレーター線 1 本として再現する。正常帯 2 本（因果ローリング分位・点線）と
    外れ値水準 4 本（経験的 ext / GPD 外挿・破線）を重ねる。subwindow の y 範囲は元
    indicator_minimum 0 〜
    indicator_maximum 100 に合わせる。RSI 線の凡例は元 ``IndicatorShortName`` の
    "RSI-{適用価格名} ({period})"（Apply で適用価格名が変わる）を再現する。
    具体描画ライブラリ（matplotlib）を core/成果物層へ侵入させない（依存内向き）。

元 MQL4 対応:
    ``#property indicator_separate_window`` + ``SetIndexStyle(0, DRAW_LINE)`` +
    ``indicator_color1 clrLime``、OnInit の ``IndicatorShortName`` switch（Apply→
    "RSI-Open/High/Low/Median/Typical/Weighted close/Close price (period)"）、
    ``indicator_minimum 0`` / ``indicator_maximum 100``。元 ExtMABuffer（EMA 平滑線）と
    σ 7 水準（StDevA1..A6 ＋ 中央線 50）は非対応（SPEC §2 / §5.4・承認 2026-08-02）。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: rsi, levels, core
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common.event_quantiles import DEFAULT_K_EVENTS, DEFAULT_Q_OUT

from .core import DEFAULT_APPLY, DEFAULT_RSI_PERIOD
from .levels import DEFAULT_Q_HIGH, DEFAULT_Q_LOW, DEFAULT_WINDOW_N
from .rsi import LEVEL_COLUMNS, RSI_COLUMN, build_rsi, quantile_column

_RSI_COLOR = "#00ff00"       # 元 indicator_color1 clrLime
_QUANTILE_COLOR = "#26c6da"  # 正常帯（lwc の _QUANTILE_COLOR と同色）
_EVQ_COLOR = "#d2433a"       # 経験的極端分位（共有 EVQ_COLOR と同色）
_GPD_COLOR = "#ffa726"       # GPD 外挿（lwc の _GPD_COLOR と同色）

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
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: "float | None" = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    title: str = "PRO!fitRSI",
) -> str:
    """RSI 線と正常帯・外れ値水準を別ペイン風に PNG 出力する。

    上段: 終値（参照）。下段: RSI 線（Lime）＋ 正常帯 2 本（点線シアン）＋ 外れ値水準
    4 本（経験的＝赤系破線 / GPD 外挿＝琥珀破線）。下段 y 範囲は [0,100]。warm-up
    （i<rsi_period）は元 iRSI 既定どおり 0 で描画される（NaN 無し）。RSI 線の凡例は
    "RSI-{適用価格名} ({period})"（Apply 依存）。

    Args:
        df: OHLC DataFrame（open/high/low/close 必須・**volume 不要**）。
        out_path: 出力 PNG パス。
        rsi_period: RSI 期間（既定 6）。
        apply: 適用価格選択（既定 5 -> Typical price）。
        window_n / q_low / q_high / q_out / k_events: 水準パラメータ（``levels`` 参照）。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    built = build_rsi(
        df, rsi_period=rsi_period, apply=apply, window_n=window_n,
        q_low=q_low, q_high=q_high, q_out=q_out, k_events=k_events,
    )
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
    # 正常帯（因果ローリング分位・点線シアン）。
    for q in (q_low, q_high):
        name = quantile_column(q)
        ax_ind.plot(x, built[name].to_numpy(dtype=np.float64), color=_QUANTILE_COLOR,
                    linewidth=0.9, linestyle=":", label=name)
    # 外れ値水準（経験的 ext ＝赤系 / GPD 外挿＝琥珀・いずれも破線）。
    for key, color in (("ext_hi", _EVQ_COLOR), ("ext_lo", _EVQ_COLOR),
                       ("gpd_hi", _GPD_COLOR), ("gpd_lo", _GPD_COLOR)):
        name = LEVEL_COLUMNS[key]
        ax_ind.plot(x, built[name].to_numpy(dtype=np.float64), color=color,
                    linewidth=1.0, linestyle="--", label=name)
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
