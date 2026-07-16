"""outlier_policy — OHLC 外れ値（配信欠損の不正ティック）判定/補正の単一定義（ISSUE-094 🔴-3）。

同一アクター（データ品質・±30%・2025-08-26 JP225 不良値対策）の外れ値補正が、従来は
書込側 :func:`marketdata.cleaning.repair_ohlc_outliers`（median([o,h,l,c]) 基準）と読取側
:func:`marketdata.dataset._clamp_outlier_bars`（min/max(open,close) エンベロープ基準）で
**別の式**として二重実装されていた。本モジュールが閾値と両戦略の唯一の定義であり、両呼び出し側は
本モジュールへ委譲する。

閾値の単一化:
    ``OUTLIER_THRESHOLD``（±30%）を両戦略の唯一の規約源とする（旧: cleaning の threshold=0.3
    既定と dataset.OUTLIER_CLAMP_THRESHOLD=0.3 の二重定義）。

式は 2 戦略として同居（実測に基づく裁定事項・下記）:
    実データ（jp225_daily / jp225_m1 / jp225_tick）で両式を全 TF に走らせた実測（ISSUE-094 3a）:
      - jp225_m1 は全 TF で両式とも補正 0・乖離 0。
      - jp225_tick は 1D/1W/1M で両式とも 8/26 バーを補正し **結果一致**（乖離 0）。
      - jp225_tick の 1h/4h で計 4 バー乖離。いずれも open/close が異なる価格帯にまたがる
        「二相バー」で、median 式が実在しない中間値（~28,700）へ 4 値を潰す（明白な誤検出）。
        エンベロープ式は当該バーを不変に保つ。
    生産経路では両式は乖離しない（書込＝日足で一致・読取＝供給バー）。ただし median 式は単一の
    不正 open/close も補正でき、エンベロープ式は low/high のみ補正し二相バーを保全する——
    **入力空間全体では優劣を断定できない**（median は二相バーで誤り・エンベロープは不正 open を放置）。
    よって式は一本化せず 2 つの命名戦略として同居させ、閾値と共通規約のみ単一化する。
    式の統一是非は裁定事項として上申する（憶測で確定しない・既存両テストが各式を byte 固定）。

依存方向: 本モジュールは stdlib + numpy/pandas + :mod:`marketdata.port` のみに依存する
（marketdata 最下層 peer・cleaning / dataset を逆 import しない・循環禁止）。
"""

from __future__ import annotations

import datetime as _dt
from statistics import median
from typing import List, Tuple

import numpy as np
import pandas as pd

from marketdata.port import Candle

# 外れ値許容相対乖離（±30%）の唯一の規約源。指数は 1 本の足内で中央値/始終値比 ±30% も
# 動かない性質を利用し、配信欠損の外れヒゲのみを分離する（両戦略が共有する単一閾値）。
OUTLIER_THRESHOLD = 0.3

# candles / DataFrame の必須 OHLC 列（小文字正規化後）。
_OHLC_COLUMNS = ("open", "high", "low", "close")


# --------------------------------------------------------------------------- #
# 戦略 A: acquisition（取得/書込時）— 足内 4 値の中央値基準（median[o,h,l,c]）。
#   単一の不正 open/high/low/close を中央値で置換し、OHLC 不変条件を再確立する。
#   補正ログを返す（人可読の監査記録）。書込パイプライン（raw 日足）で用いる。
# --------------------------------------------------------------------------- #
def repair_ohlc_outliers_median(
    candles: List[Candle], *, threshold: float = OUTLIER_THRESHOLD
) -> Tuple[List[Candle], List[str]]:
    """足内 OHLC の外れ値を中央値基準で検出・補正する（純粋・acquisition 戦略）。

    足内 4 値の中央値から ``threshold`` を超えて乖離する値を中央値で置換し、OHLC 不変条件
    （``low=min``・``high=max``）を再確立する。行を削除せず該当値のみ補正する。

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


# --------------------------------------------------------------------------- #
# 戦略 B: serving（返却/読取時）— min/max(open,close) エンベロープ基準。
#   open/close を外れにくい基準とし、low/high のみをエンベロープにクランプする。
#   正常バーは同一オブジェクト返却（冪等・キャッシュ非破壊）。供給パイプラインで用いる。
# --------------------------------------------------------------------------- #
def clamp_ohlc_envelope(
    df: pd.DataFrame, *, threshold: float = OUTLIER_THRESHOLD
) -> pd.DataFrame:
    """各行(バー)の low/high を open/close エンベロープにクランプして返す（純粋・serving 戦略）。

    補正規約（``threshold``＝±30% 既定）:
      - ``ref_lo=min(open,close)`` / ``ref_hi=max(open,close)``（open/close は外れにくい）。
      - ``low  < ref_lo*(1-threshold)`` → low を ref_lo にクランプ（下ヒゲ外れ＝配信欠損）。
      - ``high > ref_hi*(1+threshold)`` → high を ref_hi にクランプ（上ヒゲ外れ）。
      - 正常バー（±threshold 以内のヒゲ）は完全に不変（no-op で同一オブジェクトを返す）。
      - open/close が NaN/非正（ref_lo<=0）、low/high が NaN の行は防御的にスキップする。

    ベクトル化（pandas/numpy）で O(n)。冪等（再適用で不変）。ソース df は不破壊
    （補正が必要なときのみ ``df.copy()`` 上で書き換える）。OHLC 列が揃わない df は素通し。
    """
    lower_map = {str(col).lower(): col for col in df.columns}
    if not all(k in lower_map for k in _OHLC_COLUMNS):
        # OHLC 列が揃わない df は補正対象外（防御・素通し）。
        return df

    open_ = pd.to_numeric(df[lower_map["open"]], errors="coerce")
    high = pd.to_numeric(df[lower_map["high"]], errors="coerce")
    low = pd.to_numeric(df[lower_map["low"]], errors="coerce")
    close = pd.to_numeric(df[lower_map["close"]], errors="coerce")

    ref_lo = np.minimum(open_, close)
    ref_hi = np.maximum(open_, close)
    # 有効行: open/close/low/high が数値かつ ref_lo>0（0/NaN/非正は誤補正を避けスキップ）。
    valid = (
        open_.notna() & close.notna() & low.notna() & high.notna() & (ref_lo > 0)
    )
    low_mask = valid & (low < ref_lo * (1.0 - threshold))
    high_mask = valid & (high > ref_hi * (1.0 + threshold))

    if not (bool(low_mask.any()) or bool(high_mask.any())):
        # 正常バーのみ＝補正不要。コピーせず同一オブジェクトを返す（キャッシュ非破壊）。
        return df

    out = df.copy()
    if bool(low_mask.any()):
        out.loc[low_mask, lower_map["low"]] = ref_lo[low_mask]
    if bool(high_mask.any()):
        out.loc[high_mask, lower_map["high"]] = ref_hi[high_mask]
    return out


__all__ = [
    "OUTLIER_THRESHOLD",
    "repair_ohlc_outliers_median",
    "clamp_ohlc_envelope",
]
