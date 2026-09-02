"""DataFrame から値を取り出す規則（ISSUE-311 / ISSUE-314）。

本モジュールは「列名の大小を問わず必要な列を取り出す」という**規則**だけを持つ。
時刻系列の解決（:func:`resolve_times`）と、必須列の一括抽出（:func:`extract_columns`）は
同じ規則（小文字化した写像で照合し、元の列名で引く）に基づく。

---

時刻系列の解決規則（ISSUE-311）。

``DataFrame`` から「その行の時刻」を取り出す規則は 1 つしかない:

    明示指定の列 > time 列 > date 列 > ``DatetimeIndex``

この規則を各指標スライスの ``src/lwc_chart.py`` が個別に実装しており、うち 5 スライスは
1 文字も違わない複製だった（codescan 実測）。規則が変わったとき複製の一部だけが直る事故を
構造的に防ぐため、規則そのものを本モジュールに 1 つだけ置く。

本モジュールは pandas のみに依存し、描画ライブラリ・指標実装を一切知らない
（最下層＝ marketdata に置く理由）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def extract_columns(
    df: pd.DataFrame, required: "tuple[str, ...]"
) -> "tuple[np.ndarray, ...]":
    """必須列を小文字正規化して抽出する（ISSUE-314）。

    3 指標（``profit_mfi`` / ``profit_mfi_macd`` / ``profit_rmm_macd``）が 1 文字も違わない
    実装を各 src に持っていたため、規則をここへ集約した。

    Args:
        df: 入力 DataFrame（列名の大小不問）。
        required: 必須列名（小文字）。返り値の並びは本引数の順に一致する。

    Returns:
        ``required`` と同じ並びの float64 ndarray タプル。

    Raises:
        KeyError: 必須列のいずれかが欠落している場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    missing = [c for c in required if c not in lower_map]
    if missing:
        raise KeyError(f"必須列が欠落しています: {missing}")
    return tuple(df[lower_map[c]].to_numpy(dtype=np.float64) for c in required)


def resolve_times(df: pd.DataFrame, time_column: "str | None") -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > ``DatetimeIndex`` の順）。

    Args:
        df: 対象の DataFrame。
        time_column: 明示指定する時刻列名（大小不問）。``None`` なら規約順で探す。

    Returns:
        0 起点に振り直した datetime の Series。

    Raises:
        KeyError: 指定列が存在しない場合、または時刻を解決できない場合。
    """
    lower_map = {c.lower(): c for c in df.columns}
    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        return pd.to_datetime(df[tcol]).reset_index(drop=True)
    if "time" in lower_map:
        return pd.to_datetime(df[lower_map["time"]]).reset_index(drop=True)
    if "date" in lower_map:
        return pd.to_datetime(df[lower_map["date"]]).reset_index(drop=True)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, name="time").reset_index(drop=True)
    raise KeyError("時刻を解決できません（time/date 列、または DatetimeIndex が必要）。")
