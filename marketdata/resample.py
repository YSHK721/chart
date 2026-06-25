"""resample — OHLC 再集計の唯一の規則源（enabler③・dataset から物理移設）。

時間足コード（``"5m"/"1h"/"1D"`` …）→ pandas resample ルールの写像（:data:`TIMEFRAME_RULES`）と、
DataFrame を当該ルールで OHLC 再集計する :func:`resample_ohlc` を提供する。これは indicator_ui の
``dataset.resample_ohlc`` から物理移設した「唯一の規則源」であり、rollup（:mod:`marketdata.rollup`）と
indicator_ui ``dataset``（薄い再エクスポート）が共通して再利用する（再実装を禁ずる）。

依存方向（厳守）: 本モジュールは **pandas のみ** に依存し、indicator_ui を逆 import しない
（marketdata の循環依存禁止・設計 §4）。

時刻は解像度非依存。pandas 3 系では分/時は ``"5min"/"1h"``、週は取引週末（金曜ラベル ``W-FRI``）、
月末は ``"ME"``（旧 ``"M"`` は廃止）。``"1m"`` は無変換（``None``＝原子そのもの）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# candles の必須 OHLC 列（小文字正規化後）。
_OHLC_COLUMNS = ("open", "high", "low", "close")

# 時間足コード → pandas resample ルール（§チャート表示時間選択・1 分足原子）。
# 全時間足は 1 分足（原子）を resample して生成する。"1m" は無変換（None＝原子そのもの）。
# pandas 3 系では分/時は "5min"/"1h"、週は取引週末（金曜ラベル）、月末は "ME"（旧 "M" は廃止）。
# ここに無いキーはすべて拒否する（is_known_timeframe）。日足ベース dataset（sample/jp225）でも
# "1D"/"1W"/"1M" は冪等に機能する（同日 1 本の再集計は値不変）。日足未満は日足 dataset には無効
# （フロントが dataset 別に提示足を制限する）。
TIMEFRAME_RULES: dict[str, str | None] = {
    "1m": None,
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1D": "1D",
    "1W": "W-FRI",
    "1M": "ME",
}

# OHLC 集約規則（再集計時の列別 agg）。volume は合算、その他（OHLC 外）は最終値。
_OHLC_AGG = {"open": "first", "high": "max", "low": "min", "close": "last"}
_VOLUME_NAMES = ("volume", "vol")


def is_known_timeframe(timeframe: Any) -> bool:
    """timeframe がホワイトリスト（1m..1M）に存在するか（未知は False）。"""
    return timeframe in TIMEFRAME_RULES


def resample_ohlc(df: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """DataFrame を指定 pandas rule で OHLC 再集計する（§チャート表示時間選択・1 分足原子）。

    ``rule=None`` は無変換で同一 DataFrame を返す（原子＝1 分足そのもの）。それ以外は
    resample し、open=最初/high=最大/low=最小/close=最終、volume=合算、その他列=最終値で
    集約する。取引の無い期間（OHLC が NaN の行）は除去する（resample は連続区間を埋めるため、
    休場区間の空行を落とす）。
    """
    if rule is None:
        return df
    agg: dict[Any, str] = {}
    for col in df.columns:
        lc = str(col).lower()
        if lc in _OHLC_AGG:
            agg[col] = _OHLC_AGG[lc]
        elif lc in _VOLUME_NAMES:
            agg[col] = "sum"
        else:
            agg[col] = "last"
    resampled = df.resample(rule).agg(agg)
    lower_map = {str(c).lower(): c for c in df.columns}
    ohlc_cols = [lower_map[k] for k in _OHLC_COLUMNS if k in lower_map]
    return resampled.dropna(subset=ohlc_cols)
