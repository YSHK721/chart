"""OHLC クリーニング — 足内外れ値（不正ティック）の純粋な補正（ベンダ非依存）。"""

from __future__ import annotations

import datetime as _dt
from statistics import median
from typing import List, Tuple

from marketdata.port import Candle


def repair_ohlc_outliers(
    candles: List[Candle], *, threshold: float = 0.3
) -> Tuple[List[Candle], List[str]]:
    """足内 OHLC の外れ値（不正ティック）を中央値基準で検出・補正する（純粋）。

    Dukascopy 配信は区間欠損で単一 OHLC 値が極端に乖離することがある（例: 2025-08-26 の
    ``low`` ≈ 15095＝当日水準 ~42600 から約 -64%）。指数は 1 本の足内で中央値比 ±30% も
    動かないため、足内 4 値の中央値から ``threshold`` を超えて乖離する値のみを不正と判定し、
    中央値で置換したうえで OHLC 不変条件（``low=min``・``high=max``）を再確立する。

    行を削除せず該当値のみ補正するため、正常な open/high/close は保持される。

    Args:
        candles: ``{time, open, high, low, close}``（time 昇順）。
        threshold: 中央値からの許容相対乖離（0.3 = ±30%）。

    Returns:
        ``(補正後 candles, 補正ログ行)``。ログ行は補正があった足のみ（日付と変更内容）。
    """
    repaired: List[Candle] = []
    log_lines: List[str] = []
    for cd in candles:
        o, h, low, c = cd["open"], cd["high"], cd["low"], cd["close"]
        ref = median([o, h, low, c])
        if ref <= 0:
            repaired.append(cd)
            continue
        # 中央値から閾値超で乖離する値を中央値で置換（不正値の隔離）。
        fixed = {
            k: (ref if abs(v / ref - 1.0) > threshold else v)
            for k, v in (("open", o), ("high", h), ("low", low), ("close", c))
        }
        # OHLC 不変条件を再確立（high=最大・low=最小）。
        fixed["high"] = max(fixed.values())
        fixed["low"] = min(fixed.values())
        if (fixed["open"], fixed["high"], fixed["low"], fixed["close"]) != (o, h, low, c):
            day = _dt.datetime.fromtimestamp(
                cd["time"], _dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
            log_lines.append(
                f"  {day}: O/H/L/C "
                f"{o:.1f}/{h:.1f}/{low:.1f}/{c:.1f} -> "
                f"{fixed['open']:.1f}/{fixed['high']:.1f}/"
                f"{fixed['low']:.1f}/{fixed['close']:.1f}"
            )
        repaired.append(  # type: ignore[typeddict-item]
            {"time": cd["time"], "volume": cd.get("volume", 0.0), **fixed}
        )
    return repaired, log_lines
