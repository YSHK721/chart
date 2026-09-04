"""indicators — 疑似VWAP 検証の指標（純関数・ISSUE-479 Wave2 M-4）。

numpy / pandas だけに依存する。素材がどこから来たか（parquet か CSV か）を知らないので、
式の検証に実データの木が要らない。セッション境界のような**規則**は引数で受け取る
（境界の唯一源は marketdata 側のセッション日モジュールであり、それを引くのは data 層の責務）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_ratio(num: pd.Series, den: pd.Series, n: int) -> np.ndarray:
    """Σnum / Σden（当該バーを含む直近 n 本・窓不足は NaN）。"""
    return (num.rolling(n).sum() / den.rolling(n).sum()).to_numpy()


def true_range(df: pd.DataFrame) -> np.ndarray:
    prev = df["close"].shift(1)
    hi = np.maximum(df["high"], prev)
    lo = np.minimum(df["low"], prev)
    return (hi - lo).to_numpy()


def forward_return(close: np.ndarray, h: int) -> np.ndarray:
    """t → t+h の対数リターン（末尾 h 本は NaN）。"""
    r = np.full(close.size, np.nan, dtype="float64")
    r[: close.size - h] = np.log(close[h:] / close[: close.size - h])
    return r


def forward_rv(close: np.ndarray, h: int) -> np.ndarray:
    """t+1..t+h の 1 本リターン二乗和の平方根（実現ボラ・末尾 h 本は NaN）。"""
    step = np.full(close.size, np.nan, dtype="float64")
    step[1:] = np.log(close[1:] / close[:-1])
    sq = np.nan_to_num(np.square(step), nan=0.0)
    out = np.full(close.size, np.nan, dtype="float64")
    csum = np.cumsum(sq)
    lim = close.size - h
    if lim > 0:
        out[:lim] = np.sqrt(np.maximum(csum[h:] - csum[:lim], 0.0))
    return out


def session_vwap(df: pd.DataFrame, starts: np.ndarray) -> np.ndarray:
    """セッション日起点で累積する疑似VWAP（本来の VWAP 用法・日付でリセット）。

    ``starts`` は各バーが属するセッション日の始端（data 層が marketdata の唯一源から引く）。
    """
    t = np.asarray(starts)
    pv = df["pv"].to_numpy(dtype="float64")
    vol = df["volume"].to_numpy(dtype="float64")
    out = np.empty(t.size, dtype="float64")
    cum_pv = cum_v = 0.0
    prev = None
    for i in range(t.size):
        if starts[i] != prev:
            cum_pv = cum_v = 0.0
            prev = starts[i]
        cum_pv += pv[i]
        cum_v += vol[i]
        out[i] = cum_pv / cum_v if cum_v > 0 else np.nan
    return out


def session_vwap_and_index(
    df: pd.DataFrame, starts: np.ndarray
) -> "tuple[np.ndarray, np.ndarray]":
    """セッション開始からの累積 疑似VWAP と、セッション内での通し本数（0 始まり）。"""
    t = np.asarray(starts)
    pv = df["pv"].to_numpy(dtype="float64")
    vol = df["volume"].to_numpy(dtype="float64")
    out = np.empty(t.size, dtype="float64")
    idx = np.empty(t.size, dtype="int64")
    cum_pv = cum_v = 0.0
    k = 0
    prev = None
    for i in range(t.size):
        if starts[i] != prev:
            cum_pv = cum_v = 0.0
            k = 0
            prev = starts[i]
        cum_pv += pv[i]
        cum_v += vol[i]
        out[i] = cum_pv / cum_v if cum_v > 0 else np.nan
        idx[i] = k
        k += 1
    return out, idx


def session_cum_mean(df: pd.DataFrame, col: str, starts: np.ndarray) -> np.ndarray:
    """セッション開始からの累積単純平均（OHLCV だけで作れる当日版の対照）。"""
    t = np.asarray(starts)
    v = df[col].to_numpy(dtype="float64")
    out = np.empty(t.size, dtype="float64")
    s = 0.0
    c = 0
    prev = None
    for i in range(t.size):
        if starts[i] != prev:
            s = 0.0
            c = 0
            prev = starts[i]
        s += v[i]
        c += 1
        out[i] = s / c
    return out
