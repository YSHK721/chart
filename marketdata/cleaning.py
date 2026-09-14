"""OHLC クリーニング — 足内外れ値（不正ティック）の純粋な補正（ベンダ非依存）。

外れ値判定/補正の実体は :mod:`marketdata.outlier_policy`（閾値・補正コアの唯一の定義・ISSUE-094
🔴-3 / ISSUE-095 項目1）へ移設した。本モジュールの :func:`repair_ohlc_outliers` は単一の
エンベロープ補正コア（min/max(open,close) 基準）への薄い委譲へ降格する（公開シグネチャ・
返り値・ログ形式は不変）。
"""

from __future__ import annotations

from typing import List, Tuple

from marketdata import outlier_policy
from marketdata.port import Candle


def repair_ohlc_outliers(
    candles: List[Candle], *, threshold: float = outlier_policy.OUTLIER_THRESHOLD
) -> Tuple[List[Candle], List[str]]:
    """足内 OHLC の外れ値（不正ティック）をエンベロープ基準で検出・補正する（純粋）。

    Dukascopy 配信は区間欠損で単一 OHLC 値が極端に乖離することがある（例: 2025-08-26 の
    ``low`` ≈ 15095＝当日水準 ~42600 から約 -64%）。``ref_lo=min(open,close)`` /
    ``ref_hi=max(open,close)`` を外れにくい基準とし、``low < ref_lo*(1-threshold)`` の下ヒゲ・
    ``high > ref_hi*(1+threshold)`` の上ヒゲのみを ref_lo / ref_hi へクランプする（open/close は不変）。

    行を削除せず該当値のみ補正するため、正常な open/close は保持される。二相バー（open/close が
    別価格帯にまたがるバー）を保全する（ISSUE-095 項目1・裁定＝エンベロープ式へ統一）。

    実装は :func:`marketdata.outlier_policy.repair_ohlc_outliers_envelope`（単一補正コアへの委譲）で行う。

    Args:
        candles: ``{time, open, high, low, close}``（time 昇順）。
        threshold: エンベロープからの許容相対乖離（0.3 = ±30%）。

    Returns:
        ``(補正後 candles, 補正ログ行)``。ログ行は補正があった足のみ（日付と変更内容）。
    """
    return outlier_policy.repair_ohlc_outliers_envelope(candles, threshold=threshold)
