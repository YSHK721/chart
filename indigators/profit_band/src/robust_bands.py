"""統計的に頑健化したバンド生成（評価で実証した 2 欠陥への対処版）。

`bands.build_bands`（大域・絶対値）の欠陥を是正する代替計算層。既存 `build_bands` は
変更せず、本モジュールを別系統として追加する。

是正点:
  1. **スケール不変化**: 値幅を絶対価格でなく比率で正規化する。
     - ``normalize="return"``: 値幅 / 始値（フラクショナル・リターン）。
     - ``normalize="atr"``  : 値幅 / ATR（局所ボラティリティ単位）。
  2. **先読み除去（因果窓）**: 分位点を全期間ではなく「その足までの過去」から逐次算出する。
     - ``window="expanding"``: bars[0..i]。
     - ``window=<int>``      : 直近 N 本の rolling 窓。
     初期 ``min_obs`` 未満の足は NaN（確定不能）。

出力は `build_bands` と同形の ``{bucket}_{percent}`` 列だが、各列は**時変オフセット**
（足ごとに更新）であり、不確定な初期区間は NaN。lightweight-charts 連携は
`lwc_chart.add_robust_profit_band` を使う。
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple, Union

import numpy as np
import pandas as pd

from .core import PROBABILITIES

# 描画対象の 4 系統。bands._BAND_SIGNS と同一の符号・bucket→(値幅, 分類, 符号) 対応。
# 値幅: oh=|open-high|, ol=|open-low| / 分類: bull=陽線, bear=陰線, even=同値
# 符号: +1=始値の上側, -1=始値の下側（core.collect_distance_samples と整合）。
DEFAULT_BUCKETS: Tuple[str, ...] = ("nOH", "pOL", "pOH", "nOL")


def _percent_tag(probability: float) -> str:
    return str(int(round(probability * 100)))


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """単純移動平均ベースの ATR。先頭 period-1 本は NaN。

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)。
    """
    n = len(high)
    prev_c = np.empty(n)
    prev_c[0] = close[0]
    prev_c[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr = np.full(n, np.nan)
    if n >= period:
        csum = np.cumsum(tr)
        atr[period - 1:] = (csum[period - 1:] - np.concatenate([[0.0], csum[:-period]])) / period
    return atr


def _causal_quantiles(
    norm: np.ndarray,
    contrib: np.ndarray,
    probs: np.ndarray,
    window: Union[str, int],
    min_obs: int,
) -> np.ndarray:
    """各足 i について、因果窓内の正規化サンプルから分位点を算出する。

    Args:
        norm: 各足の正規化値幅（非対象足は NaN）。
        contrib: その足がサンプルを供出するか（分類一致かつ有限）。
        probs: 確率配列。
        window: "expanding" または rolling 窓幅(int)。
        min_obs: 分位点確定に要する最小標本数。

    Returns:
        shape (n, len(probs)) の分位点。標本不足は NaN。
    """
    n = norm.shape[0]
    out = np.full((n, probs.shape[0]), np.nan)
    idx = np.where(contrib)[0]
    vals = norm[idx]
    expanding = window == "expanding"
    W = None if expanding else int(window)
    for i in range(n):
        if expanding:
            m = idx <= i
        else:
            m = (idx <= i) & (idx > i - W)
        if int(m.sum()) >= min_obs:
            out[i] = np.quantile(vals[m], probs, method="linear")
    return out


def build_robust_bands(
    df: pd.DataFrame,
    *,
    probabilities: Iterable[float] = PROBABILITIES,
    buckets: Iterable[str] = DEFAULT_BUCKETS,
    normalize: str = "return",
    window: Union[str, int] = "expanding",
    atr_period: int = 14,
    min_obs: int = 30,
) -> pd.DataFrame:
    """正規化＋因果窓で統計バンドを生成する（`build_bands` の頑健版）。

    Args:
        df: ``open/high/low/close`` 列を持つ DataFrame（列名の大小不問）。
        probabilities: 確率の並び（既定 PROBABILITIES）。
        buckets: 描画系統（既定 nOH/pOL/pOH/nOL）。
        normalize: ``"return"``（値幅/始値）または ``"atr"``（値幅/ATR）。
        window: ``"expanding"`` または rolling 窓幅(int)。
        atr_period: normalize="atr" 時の ATR 期間。
        min_obs: 分位点確定に要する最小標本数（未満の足は NaN）。

    Returns:
        ``{bucket}_{percent}`` 列の DataFrame（時変・初期は NaN）。元 index を継承。

    Raises:
        KeyError: 必須 OHLC 列または未知 bucket。
        ValueError: normalize が不正な場合。
    """
    cols = {c.lower(): c for c in df.columns}
    missing = [k for k in ("open", "high", "low", "close") if k not in cols]
    if missing:
        raise KeyError(f"必須列が不足しています: {missing}（存在する列: {list(df.columns)}）")

    o = df[cols["open"]].to_numpy(float)
    h = df[cols["high"]].to_numpy(float)
    l = df[cols["low"]].to_numpy(float)
    c = df[cols["close"]].to_numpy(float)

    oh = np.abs(o - h)
    ol = np.abs(o - l)
    bull = o < c
    bear = o > c
    even = o == c

    if normalize == "return":
        scale = o.astype(float).copy()
    elif normalize == "atr":
        scale = _atr(h, l, c, atr_period)
    else:
        raise ValueError(f"normalize は 'return' か 'atr': {normalize!r}")
    scale = np.where(scale > 0, scale, np.nan)  # 0/負スケールは無効化

    # bucket -> (生値幅, 分類マスク, 符号)。core.collect_distance_samples と整合。
    spec: Dict[str, Tuple[np.ndarray, np.ndarray, int]] = {
        "pOL": (ol, bull, +1),
        "nOH": (oh, bear, -1),
        "pOH": (oh, bull | even, +1),
        "nOL": (ol, bear | even, -1),
    }

    probabilities = tuple(probabilities)
    probs = np.asarray(probabilities, dtype=float)
    data: Dict[str, np.ndarray] = {}
    for bucket in buckets:
        if bucket not in spec:
            raise KeyError(f"未知の系統です: {bucket}（有効: {list(spec)}）")
        raw, mask, sign = spec[bucket]
        norm = raw / scale                       # 正規化値幅（scale NaN→NaN）
        contrib = mask & np.isfinite(norm)        # 標本を供出する足
        q = _causal_quantiles(norm, contrib, probs, window, min_obs)  # (n, P)
        for k, prob in enumerate(probabilities):
            # 価格オフセットへ復元: return→ open*(1±q), atr→ open ± q*ATR
            data[f"{bucket}_{_percent_tag(prob)}"] = o + sign * q[:, k] * scale

    return pd.DataFrame(data, index=df.index)
