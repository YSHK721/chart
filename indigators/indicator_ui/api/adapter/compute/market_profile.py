"""market_profile — 足ベース TPO マーケットプロファイルの計算コア（純関数）。

本モジュールは I/O を一切持たない純関数のみを提供する。データ読込（dataset.load_candles
との配線）や API・フロント連携は本モジュールの責務外（後続作業）。
"""

from __future__ import annotations

import numpy as np


def price_range(candles) -> tuple[float, float]:
    """candles の価格レンジ ``(price_min, price_max)`` を返す（価格レンジ定義の単一情報源）。

    ``compute_candle_profile`` 内部・controller の barw 経路が共有する。空リストは
    ``(0.0, 0.0)`` の安全値。レンジ縮退（``price_max <= price_min``）時はゼロ割回避のため
    ``price_max = price_min + 1`` に安全化する（compute_candle_profile と同一挙動）。

    Args:
        candles: OHLC 辞書リスト [{"low","high", ...}, ...]。

    Returns:
        (price_min, price_max) の float タプル。
    """
    if not candles:
        return 0.0, 0.0
    price_min = float(min(c["low"] for c in candles))
    price_max = float(max(c["high"] for c in candles))
    if price_max <= price_min:
        price_max = price_min + 1
    return price_min, price_max


def compute_candle_profile(candles, n_bins=60, va_pct=0.70) -> dict:
    """足ベース TPO マーケットプロファイルを計算する（純関数・副作用なし）。

    Args:
        candles: OHLC 辞書リスト [{"time","open","high","low","close"}, ...]（time 昇順）。
        n_bins: 価格ビン分割数。
        va_pct: バリューエリア比率（0..1）。

    Returns:
        {"bins","poc","va_low","va_high","price_min","price_max","tpo_units","n_bins"}
    """
    # 空リストは例外でなく空/ゼロの安全な返りを返す（poc 等は price_min=0.0 の安全値）。
    if not candles:
        return {
            "bins": [],
            "poc": 0.0,
            "va_low": 0.0,
            "va_high": 0.0,
            "price_min": 0.0,
            "price_max": 0.0,
            "tpo_units": 0,
            "n_bins": n_bins,
        }

    # 価格レンジ（縮退時の +1 安全化を含め price_range に一元化＝単一情報源）。
    price_min, price_max = price_range(candles)

    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    span = price_max - price_min

    tpo = np.zeros(n_bins, dtype=int)
    for c in candles:
        i0 = _bin_index(c["low"], price_min, span, n_bins)
        i1 = _bin_index(c["high"], price_min, span, n_bins)
        if i1 < i0:  # 縮退時は i0 のみ
            i1 = i0
        tpo[i0 : i1 + 1] += 1

    tpo_max = int(tpo.max())
    poc = float(centers[int(tpo.argmax())])  # argmax は同値時に先頭 index を返す
    va_low, va_high = _value_area(tpo, centers, va_pct)

    bins = [
        {
            "price": round(float(centers[i]), 2),
            "tpo": int(tpo[i]),
            "norm": round(float(tpo[i]) / tpo_max, 4),
        }
        for i in range(n_bins)
    ]

    return {
        "bins": bins,
        "poc": round(poc, 2),
        "va_low": round(va_low, 2),
        "va_high": round(va_high, 2),
        "price_min": float(price_min),
        "price_max": float(price_max),
        "tpo_units": len(candles),
        "n_bins": n_bins,
    }


def _value_area(tpo, centers, va_pct):
    """バリューエリアの下限/上限中心価格を返す。

    tpo 降順（同値時は index 昇順で決定論化）にビンを積み、累積が総 tpo×va_pct に
    達するまでのビン集合の中心価格の最小/最大を (va_low, va_high) として返す。
    """
    threshold = int(tpo.sum()) * va_pct
    order = sorted(range(len(tpo)), key=lambda i: (-tpo[i], i))
    va_centers = []
    cum = 0
    for i in order:
        va_centers.append(float(centers[i]))
        cum += int(tpo[i])
        if cum >= threshold:
            break
    return min(va_centers), max(va_centers)


def _bin_index(price, price_min, span, n_bins) -> int:
    """price が属するビン index を返す（[0, n_bins-1] にクランプ）。"""
    idx = int((price - price_min) / span * n_bins)
    if idx < 0:
        return 0
    if idx >= n_bins:
        return n_bins - 1
    return idx
