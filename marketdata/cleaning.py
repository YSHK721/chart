"""OHLC クリーニング — 足内外れ値（不正ティック）の純粋な補正（ベンダ非依存）。

外れ値判定/補正の実体は :mod:`marketdata.outlier_policy`（閾値・両戦略の唯一の定義・ISSUE-094
🔴-3）へ移設した。本モジュールの :func:`repair_ohlc_outliers` は acquisition 戦略
（median([o,h,l,c]) 基準）への薄い委譲へ降格する（公開シグネチャ・返り値・ログ形式は byte 不変）。
"""

from __future__ import annotations

from typing import List, Tuple

from marketdata import outlier_policy
from marketdata.port import Candle


def repair_ohlc_outliers(
    candles: List[Candle], *, threshold: float = outlier_policy.OUTLIER_THRESHOLD
) -> Tuple[List[Candle], List[str]]:
    """足内 OHLC の外れ値（不正ティック）を中央値基準で検出・補正する（純粋）。

    Dukascopy 配信は区間欠損で単一 OHLC 値が極端に乖離することがある（例: 2025-08-26 の
    ``low`` ≈ 15095＝当日水準 ~42600 から約 -64%）。指数は 1 本の足内で中央値比 ±30% も
    動かないため、足内 4 値の中央値から ``threshold`` を超えて乖離する値のみを不正と判定し、
    中央値で置換したうえで OHLC 不変条件（``low=min``・``high=max``）を再確立する。

    行を削除せず該当値のみ補正するため、正常な open/high/close は保持される。

    実装は :func:`marketdata.outlier_policy.repair_ohlc_outliers_median`（acquisition 戦略）へ委譲する。

    Args:
        candles: ``{time, open, high, low, close}``（time 昇順）。
        threshold: 中央値からの許容相対乖離（0.3 = ±30%）。

    Returns:
        ``(補正後 candles, 補正ログ行)``。ログ行は補正があった足のみ（日付と変更内容）。
    """
    return outlier_policy.repair_ohlc_outliers_median(candles, threshold=threshold)
