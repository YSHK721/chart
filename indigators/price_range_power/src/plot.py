"""出力アダプタ: matplotlib による価格帯プロファイル PNG 描画。

層名/責務:
    出力アダプタ。計算は ratio 層に委譲し、本層は「取り出し→描画」のみ。
    ヘッドレスで完結（Agg）。元 VBA はシート上に表＋書式（displayFormatSet）で示したが、
    Python では価格帯（Y 軸）に対する度数・ブル/ベア勢力の水平プロファイルとして可視化する。

元 VBA の対応:
    ``pD.iDataWrite(res)`` + ``DF.DF_PricerangePower``（表＋色分け表示）。
    本図はその表を価格帯プロファイル図として再表現したもの。

依存:
    標準: __future__ / 外部: numpy, pandas, matplotlib / プロジェクト内: ratio, core
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス出力
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .core import DEFAULT_INTERVAL
from .ratio import build_bull_bear_profile

_BULL_COLOR = "#2e9e5b"   # ブル（支持帯）
_BEAR_COLOR = "#d2433a"   # ベア（抵抗帯）
_FREQ_COLOR = "#9e9e9e"   # 度数（出現頻度）


def plot_price_range_power(
    df: pd.DataFrame,
    out_path: str = "price_range_power.png",
    *,
    interval: float = DEFAULT_INTERVAL,
    range_from: float | None = None,
    range_to: float | None = None,
    title: str = "PriceRangePower (price-band bull/bear ratio)",
) -> str:
    """価格帯別のブル/ベア勢力プロファイルを PNG に出力する。

    左パネル: 各価格帯の出現度数（安値=左, 高値=右）。
    右パネル: ブル勢力（OL/LH%, 右向き）とベア勢力（HC/HL%, 左向き）の水平バー。

    Args:
        df: OHLC DataFrame。
        out_path: 出力 PNG パス。
        interval/range_from/range_to: build_price_range_power と同じ意味。
        title: 図のタイトル。

    Returns:
        書き出した PNG のパス。
    """
    prof = build_bull_bear_profile(
        df, interval=interval, range_from=range_from, range_to=range_to
    )
    bands = prof.index.to_numpy(dtype=np.float64)
    height = interval * 0.9

    fig, (ax_freq, ax_power) = plt.subplots(
        1, 2, figsize=(13, 9), sharey=True, gridspec_kw={"width_ratios": [1, 1.6]}
    )

    # 左: 度数プロファイル（安値=左方向, 高値=右方向）。
    ax_freq.barh(bands, -prof["freq_low"].to_numpy(), height=height,
                 color=_BULL_COLOR, alpha=0.45, label="freq low")
    ax_freq.barh(bands, prof["freq_high"].to_numpy(), height=height,
                 color=_BEAR_COLOR, alpha=0.45, label="freq high")
    ax_freq.axvline(0, color="#333333", linewidth=0.8)
    ax_freq.set_title("frequency  (low <-- / --> high)")
    ax_freq.set_xlabel("count")
    ax_freq.set_ylabel("price band (prp)")
    ax_freq.legend(loc="upper left", fontsize=8)
    ax_freq.grid(True, axis="x", alpha=0.2)

    # 右: ブル/ベア勢力（ブル=右, ベア=左）。
    ax_power.barh(bands, prof["bull_power"].to_numpy(), height=height,
                  color=_BULL_COLOR, alpha=0.8, label="bull power (OL/LH%)")
    ax_power.barh(bands, -prof["bear_power"].to_numpy(), height=height,
                  color=_BEAR_COLOR, alpha=0.8, label="bear power (HC/HL%)")
    ax_power.axvline(0, color="#333333", linewidth=0.8)
    ax_power.set_title("bull / bear power  (bear <-- / --> bull)")
    ax_power.set_xlabel("sum of sigma-bucket ratios")
    ax_power.legend(loc="upper left", fontsize=8)
    ax_power.grid(True, axis="x", alpha=0.2)

    fig.suptitle(title, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
