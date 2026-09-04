"""outlier_policy — OHLC 外れ値（配信欠損の不正ティック）判定/補正の単一定義（ISSUE-094 🔴-3 / ISSUE-095 項目1）。

同一アクター（データ品質・±30%・2025-08-26 JP225 不良値対策）の外れ値補正が、従来は
書込側 :func:`marketdata.cleaning.repair_ohlc_outliers`（median([o,h,l,c]) 基準）と読取側
:func:`marketdata.dataset._clamp_outlier_bars`（min/max(open,close) エンベロープ基準）で
**別の式**として二重実装されていた。ISSUE-095 項目1（依頼者裁定＝エンベロープ式へ統一）により
補正コアを **エンベロープ式の単一コア**（:func:`clamp_ohlc_envelope`）へ一本化した。両呼び出し側
（acquisition / serving）は本コアへ委譲する。

閾値の単一化:
    ``OUTLIER_THRESHOLD``（±30%）を唯一の規約源とする（旧: cleaning の threshold=0.3 既定と
    dataset.OUTLIER_CLAMP_THRESHOLD=0.3 の二重定義）。

補正式の一本化（ISSUE-095 項目1・裁定＝エンベロープ）:
    唯一の補正式は ``ref_lo=min(open,close)`` / ``ref_hi=max(open,close)`` を外れにくい基準とし、
    ``low < ref_lo*(1-threshold)`` の下ヒゲ・``high > ref_hi*(1+threshold)`` の上ヒゲのみを
    それぞれ ref_lo / ref_hi へクランプする（open/close は不変）。実データ実測（ISSUE-094 3a）で
    旧 median 式は jp225_tick 1h/4h の二相バー 4 本（open/close が別価格帯にまたがるバー）で
    実在しない中間値（~28,700）へ 4 値を潰す誤検出だったのに対し、エンベロープ式は当該バーを保全する。
    本統一により acquisition 経路（旧 median）も serving 経路と同一の保全挙動になる。

依存方向: 本モジュールは stdlib + numpy/pandas + :mod:`marketdata.port` のみに依存する
（marketdata 最下層 peer・cleaning / dataset を逆 import しない・循環禁止）。
"""

from __future__ import annotations

import datetime as _dt
from typing import List, Tuple

import numpy as np
import pandas as pd

from marketdata.port import Candle

# 外れ値許容相対乖離（±30%）の唯一の規約源。指数は 1 本の足内で中央値/始終値比 ±30% も
# 動かない性質を利用し、配信欠損の外れヒゲのみを分離する（単一閾値）。
OUTLIER_THRESHOLD = 0.3

# candles / DataFrame の必須 OHLC 列（小文字正規化後）。
_OHLC_COLUMNS = ("open", "high", "low", "close")


# --------------------------------------------------------------------------- #
# 単一補正コア: min/max(open,close) エンベロープ基準（ISSUE-095 項目1・唯一の式）。
#   open/close を外れにくい基準とし、low/high のみをエンベロープにクランプする。
#   正常バーは同一オブジェクト返却（冪等・キャッシュ非破壊）。serving / acquisition の両経路が委譲する。
# --------------------------------------------------------------------------- #
def clamp_ohlc_envelope(
    df: pd.DataFrame, *, threshold: float = OUTLIER_THRESHOLD
) -> pd.DataFrame:
    """各行(バー)の low/high を open/close エンベロープにクランプして返す（純粋・単一補正コア）。

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


# --------------------------------------------------------------------------- #
# acquisition アダプタ（取得/書込時）— candles を DataFrame 化し単一コアへ委譲する。
#   OHLC 不変条件の再確立ではなく、open/close エンベロープでの low/high クランプのみ行う
#   （serving と同一挙動）。補正ログ（人可読の監査記録）を合成して返す。
# --------------------------------------------------------------------------- #
def repair_ohlc_outliers_envelope(
    candles: List[Candle], *, threshold: float = OUTLIER_THRESHOLD
) -> Tuple[List[Candle], List[str]]:
    """足内 OHLC の外れ値をエンベロープ基準で検出・補正する（純粋・acquisition アダプタ）。

    :func:`clamp_ohlc_envelope`（単一補正コア）へ委譲し、``ref_lo=min(open,close)`` /
    ``ref_hi=max(open,close)`` を外れにくい基準として low/high のみをクランプする。open/close は
    不変。行を削除せず該当値のみ補正する。二相バー（open/close が別価格帯にまたがるバー）は保全する。

    Returns:
        ``(補正後 candles, 補正ログ行)``。ログ行は補正があった足のみ（日付と変更内容）。
    """
    repaired: List[Candle] = []
    log_lines: List[str] = []
    if not candles:
        return repaired, log_lines

    # 単一コア（serving と同一の式）へ委譲するため candles を DataFrame 化する。
    df = pd.DataFrame(
        {
            "open": [cd["open"] for cd in candles],
            "high": [cd["high"] for cd in candles],
            "low": [cd["low"] for cd in candles],
            "close": [cd["close"] for cd in candles],
        }
    )
    clamped = clamp_ohlc_envelope(df, threshold=threshold)

    for i, cd in enumerate(candles):
        o, h, low, c = cd["open"], cd["high"], cd["low"], cd["close"]
        new_high = float(clamped["high"].iloc[i])
        new_low = float(clamped["low"].iloc[i])
        # エンベロープ式は open/close を変えず low/high のみをクランプする。
        if (new_high, new_low) != (h, low):
            day = _dt.datetime.fromtimestamp(
                cd["time"], _dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
            log_lines.append(
                f"  {day}: O/H/L/C "
                f"{o:.1f}/{h:.1f}/{low:.1f}/{c:.1f} -> "
                f"{o:.1f}/{new_high:.1f}/{new_low:.1f}/{c:.1f}"
            )
        repaired.append(  # type: ignore[typeddict-item]
            {
                "time": cd["time"],
                "volume": cd.get("volume", 0.0),
                "open": o,
                "high": new_high,
                "low": new_low,
                "close": c,
            }
        )
    return repaired, log_lines


# --------------------------------------------------------------------------- #
# 日内中央値式の外れ M1 行除去（参照実装 proto_server._repair_day_outliers /
#   simulator.replay_ui.adapter._m1_repair.repair_day_outliers と bit 一致の式）。
#   エンベロープ式（バー内 open/close 基準）は open/close 自体が不正な連続不良ラン
#   （例 2025-08-26 06:34〜09:09 UTC の ~15,100 帯）を補正できないため、M1 素材の
#   生成段（tick_m1）ではこちらの日内クロスバー基準で行ごと除去する。
# --------------------------------------------------------------------------- #
def repair_day_outliers(
    df: pd.DataFrame, threshold: float = OUTLIER_THRESHOLD
) -> pd.DataFrame:
    """日内 close 中央値から OHLC のいずれかが threshold 超で乖離する M1 行を除去する（純粋）。

    Dukascopy の区間欠損で 1 分足が極端に乖離する（例: 2025-08-26 の ~15,100＝当日 ~42,600 から
    約 -64%）外れバーのみを安全に分離する。指数は日中に中央値比 ±30% も動かないため、
    配信欠損ファントムのみが該当する。入力 df は ``DatetimeIndex``（UTC naive）＋
    OHLC 列を持つこと。正常のみなら同一オブジェクトを返す（冪等・不破壊）。
    """
    if len(df) == 0:
        return df
    day = df.index.normalize()                          # 暦日キー（UTC・tz-naive）
    med = df.groupby(day)["close"].transform("median")  # 各行＝その日の close 中央値
    dev = pd.concat(
        [(df[c] / med - 1.0).abs() for c in ("open", "high", "low", "close")], axis=1
    ).max(axis=1)
    mask = (med > 0) & (dev > threshold)
    if not bool(mask.any()):
        return df
    return df[~mask]


__all__ = [
    "OUTLIER_THRESHOLD",
    "clamp_ohlc_envelope",
    "repair_ohlc_outliers_envelope",
    "repair_day_outliers",
]
