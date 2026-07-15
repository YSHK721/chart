"""market_profile — 足ベース TPO マーケットプロファイルの計算コア（純関数）。

本モジュールは I/O を一切持たない純関数のみを提供する。データ読込（dataset.load_candles
との配線）や API・フロント連携は本モジュールの責務外（後続作業）。
"""

from __future__ import annotations

import datetime as _dt

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


def compute_candle_profile(
    candles, n_bins=60, va_pct=0.70, want_today=False, want_sessions=False
) -> dict:
    """足ベース TPO マーケットプロファイルを計算する（純関数・副作用なし）。

    Args:
        candles: OHLC 辞書リスト [{"time","open","high","low","close"}, ...]（time 昇順）。
        n_bins: 価格ビン分割数。
        va_pct: バリューエリア比率（0..1）。
        want_today: True のとき、応答に ``today[]``/``today_max`` を付加する（増分2 C スナップショット）。
            ``today[]`` は「窓の最終足ぶんの表示 bin 値」（最終足の [low,high] が跨ぐ bin に +1）。
            移植元 prototype_260630-01/mp_core.py want_today（candle=最終足の寄与）。既定 False は不変。
        want_sessions: True のとき、応答に ``sessions[]`` を付加する（日別プロファイル分割表示）。
            各カレンダー日（UTC 日付キー）の表示 bin プロファイル
            ``[{"date":"YYYY-MM-DD", "tpo":[float,...](len=n_bins)}]`` を日付昇順で返す。
            各足の [low,high] が跨ぐ表示 bin に +1 し、同一日の足を合算する
            （移植元 prototype_260630-01/mp_core.py want_sessions・candle 経路）。既定 False は不変。

    Returns:
        {"bins","poc","va_low","va_high","price_min","price_max","tpo_units","n_bins"}
        want_today=True 時は加えて {"today":[float,...](len=n_bins), "today_max":float}。
        want_sessions=True 時は加えて {"sessions":[{"date","tpo":[...]}]}。
    """
    # 空リストは例外でなく空/ゼロの安全な返りを返す（poc 等は price_min=0.0 の安全値）。
    if not candles:
        out = {
            "bins": [],
            "poc": 0.0,
            "va_low": 0.0,
            "va_high": 0.0,
            "price_min": 0.0,
            "price_max": 0.0,
            "tpo_units": 0,
            "n_bins": n_bins,
        }
        if want_today:
            out["today"] = []
            out["today_max"] = 1.0
        if want_sessions:
            out["sessions"] = []
        return out

    # 価格レンジ（縮退時の +1 安全化を含め price_range に一元化＝単一情報源）。
    price_min, price_max = price_range(candles)

    edges = np.linspace(price_min, price_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    span = price_max - price_min

    tpo = np.zeros(n_bins, dtype=int)
    # want_sessions: UTC カレンダー日 -> 表示 bin プロファイル（同一日は合算）。
    sessions: dict[str, np.ndarray] = {}
    for c in candles:
        i0 = _bin_index(c["low"], price_min, span, n_bins)
        i1 = _bin_index(c["high"], price_min, span, n_bins)
        if i1 < i0:  # 縮退時は i0 のみ
            i1 = i0
        tpo[i0 : i1 + 1] += 1
        if want_sessions:
            day = _dt.datetime.fromtimestamp(int(c["time"]), _dt.timezone.utc).strftime(
                "%Y-%m-%d"
            )
            arr = sessions.get(day)
            if arr is None:
                arr = np.zeros(n_bins, dtype=float)
                sessions[day] = arr
            arr[i0 : i1 + 1] += 1.0  # その日の [low,high] が跨ぐ表示 bin に +1（同一日は合算）。

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

    out = {
        "bins": bins,
        "poc": round(poc, 2),
        "va_low": round(va_low, 2),
        "va_high": round(va_high, 2),
        "price_min": float(price_min),
        "price_max": float(price_max),
        "tpo_units": len(candles),
        "n_bins": n_bins,
    }
    if want_today:
        # 窓の最終足ぶん（別カラー表示用）。最終足の [low,high] が跨ぐ bin に +1（試作 want_today と同義）。
        last = candles[-1]
        i0 = _bin_index(last["low"], price_min, span, n_bins)
        i1 = _bin_index(last["high"], price_min, span, n_bins)
        if i1 < i0:
            i1 = i0
        today = np.zeros(n_bins, dtype=float)
        today[i0 : i1 + 1] += 1.0
        today_max = float(today.max()) if today.max() > 0 else 1.0
        out["today"] = [round(float(v), 3) for v in today]
        out["today_max"] = today_max
    if want_sessions:
        # 日付昇順で {date, tpo[], poc, va_low, va_high} を返す。VA は累積プロファイルと同一定義
        #   （_value_area・単一定義）を各日 tpo に適用する＝当日 MP 読み取りと VA 線が一致する（DRY）。
        out["sessions"] = [
            _session_entry(d, a, centers, va_pct) for d, a in sorted(sessions.items())
        ]
    return out


def _session_entry(date, tpo, centers, va_pct):
    """1 セッションの応答 dict ``{date, tpo[], poc, va_low, va_high}`` を作る（VA は _value_area 単一定義）。"""
    va_low, va_high = _value_area(tpo, centers, va_pct)
    poc = float(centers[int(tpo.argmax())]) if float(tpo.sum()) > 0 else float(centers[0])
    return {
        "date": date,
        "tpo": [round(float(v), 2) for v in tpo],
        "poc": round(poc, 2),
        "va_low": round(va_low, 2),
        "va_high": round(va_high, 2),
    }


def _value_area(tpo, centers, va_pct):
    """バリューエリアの下限/上限中心価格を返す。

    tpo 降順（同値時は index 昇順で決定論化）にビンを積み、累積が総 tpo×va_pct に
    達するまでのビン集合の中心価格の最小/最大を (va_low, va_high) として返す。
    ISSUE-085: 重みは float で累積する。旧 ``int(tpo[i])`` 切り捨ては zp の z 値（大半が 1 未満）を
    全て 0 に潰し、累積が閾値へ届かず全ビン採用＝VA が全域へ広がっていた。整数 TPO（カウント系）は
    int/float どちらの累積でも同値＝既存挙動不変（byte-parity 維持）。
    """
    threshold = float(tpo.sum()) * va_pct
    order = sorted(range(len(tpo)), key=lambda i: (-tpo[i], i))
    va_centers = []
    cum = 0.0
    for i in order:
        va_centers.append(float(centers[i]))
        cum += float(tpo[i])
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
