"""M1 外れ値補正の共有モジュール（両 repository が public 参照・proto _repair_day_outliers 忠実）。

Dukascopy の区間欠損で 1 分足が日内 close 中央値から極端に乖離する（配信欠損ファントム）バーのみを
読み取り時に除去する（生 CSV は不変）。指数は日中に中央値比 ±30% も動かないため、閾値超のみ該当。

技術隔離（CLEAN_ARCH §6）: pandas は本ファイル内に閉じる。
"""
from __future__ import annotations

import pandas as pd

# M1 日内補正の外れ値閾値（proto_server / tick_window と同一・0.3=±30%）。
M1_OUTLIER_THRESHOLD = 0.3


def repair_day_outliers(
    df: "pd.DataFrame", threshold: float = M1_OUTLIER_THRESHOLD
) -> "pd.DataFrame":
    """日内 close 中央値から OHLC が threshold 超で乖離する M1 行を除去（proto と bit 一致）。"""
    if len(df) == 0:
        return df
    day = df.index.normalize()
    med = df.groupby(day)["close"].transform("median")
    dev = pd.concat(
        [(df[c] / med - 1.0).abs() for c in ("open", "high", "low", "close")], axis=1
    ).max(axis=1)
    mask = (med > 0) & (dev > threshold)
    return df[~mask]
