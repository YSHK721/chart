"""バンドの可視化（matplotlib）。

元 MT5 描画を再現する:
  * ローソク足（陽線/陰線）
  * nOH(下)〜pOL(上) の塗りバンド（パーセンタイルごとに濃淡を変える）
  * pOH(上)/nOL(下) の外側点線

計算は build_bands に委譲し、本モジュールは描画のみを担う。
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # ヘッドレス環境向け（ファイル出力）
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .bands import build_bands
from .core import PROBABILITIES

# パーセンタイル -> 塗りの不透明度（外側ほど薄く）。元 MT5 の濃淡指定に対応。
_FILL_ALPHA = {99: 0.10, 98: 0.12, 95: 0.15, 90: 0.18, 85: 0.22, 80: 0.26, 51: 0.32}


def _percent_tag(p: float) -> str:
    return str(int(round(p * 100)))


def plot_bands(
    df: pd.DataFrame,
    out_path: str = "profit_band.png",
    *,
    probabilities: tuple[float, ...] = PROBABILITIES,
    last_n: int | None = None,
    title: str = "PRO!fit_Band",
) -> str:
    """OHLC からバンドを計算して PNG に描画する。

    Args:
        df: open/high/low/close 列を持つ DataFrame（列名の大小不問）。
        out_path: 出力 PNG パス。
        probabilities: 描画する確率の並び。
        last_n: 直近 n 本のみ描画（None なら全件）。
        title: グラフタイトル。

    Returns:
        書き出した PNG のパス。
    """
    bands = build_bands(df, probabilities=probabilities)

    cols = {c.lower(): c for c in df.columns}
    o = df[cols["open"]].to_numpy(dtype=float)
    h = df[cols["high"]].to_numpy(dtype=float)
    l = df[cols["low"]].to_numpy(dtype=float)
    c = df[cols["close"]].to_numpy(dtype=float)

    n = len(df)
    start = 0 if last_n is None else max(0, n - last_n)
    x = np.arange(start, n)

    fig, ax = plt.subplots(figsize=(14, 7))

    # --- 塗りバンド（外側=99% から内側=51% の順で重ね描き）---
    for prob in sorted(probabilities, reverse=True):
        tag = _percent_tag(prob)
        lower = bands[f"nOH_{tag}"].to_numpy()[start:]
        upper = bands[f"pOL_{tag}"].to_numpy()[start:]
        ax.fill_between(
            x, lower, upper,
            color="navy", alpha=_FILL_ALPHA.get(int(tag), 0.15),
            linewidth=0, label=f"nOH-pOL {tag}%" if prob in (0.99, 0.51) else None,
        )

    # --- 外側点線 pOH(上)/nOL(下) ---
    for prob in probabilities:
        tag = _percent_tag(prob)
        ax.plot(x, bands[f"pOH_{tag}"].to_numpy()[start:],
                color="teal", linestyle=":", linewidth=0.8)
        ax.plot(x, bands[f"nOL_{tag}"].to_numpy()[start:],
                color="darkred", linestyle=":", linewidth=0.8)

    # --- ローソク足 ---
    for i in x:
        up = c[i] >= o[i]
        color = "#1565C0" if up else "#C62828"
        ax.plot([i, i], [l[i], h[i]], color=color, linewidth=0.6, zorder=3)
        ax.add_patch(
            plt.Rectangle(
                (i - 0.3, min(o[i], c[i])), 0.6, abs(c[i] - o[i]) or 1e-9,
                color=color, zorder=4,
            )
        )

    ax.set_title(title)
    ax.set_xlabel("bar index")
    ax.set_ylabel("price")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
