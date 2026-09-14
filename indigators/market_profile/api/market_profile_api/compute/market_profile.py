"""market_profile — 足ベース TPO マーケットプロファイルの計算コア（純関数）。

本モジュールは I/O を一切持たない純関数のみを提供する。データ読込（dataset.load_candles
との配線）や API・フロント連携は本モジュールの責務外（後続作業）。
"""

from __future__ import annotations

import datetime as _dt
import math as _math
from typing import Any as _Any

import numpy as np

# --------------------------------------------------------------------------- #
# バリューエリア比率（VA 比率）の単一情報源（ISSUE-260）
#
#   「総重みの何割を VA とみなすか」は 1 つの業務パラメータであり、決定権者は 1 つでなければ
#   ならない。かつてこの値は UI（catalog の param 既定）・``market_profile_controller``・
#   ``tf_period_columns`` の直書き・front ``DwellAccumulator`` の 4 面に独立して存在し、
#   ``/market_profile`` の非増分 refresh 以外は UI をどう操作しても 0.70 のままだった
#   （＝効かないツマミ）。以後、値の宣言は本ファイルのこの 1 行のみとする:
#     - backend の各経路は :func:`resolve_va_pct` で解決した値を**必須引数**として受け渡す。
#     - front は「生成物」（tools/gen_js_parity_golden.py → web/js/domain/mp_va_generated.js）
#       として本値を読む（catalog の既定表示）／実効値は応答（forming の ``vaPct``）に従う。
# --------------------------------------------------------------------------- #
VA_PCT_DEFAULT = 0.70
#: 有効域。退化（負・0・NaN/Inf・>1）を無言で通さず、この範囲へクランプする。
VA_PCT_MIN = 0.01
VA_PCT_MAX = 1.0


def resolve_va_pct(raw: _Any) -> float:
    """VA 比率の要求値（クエリ str / 数値 / None）を有効な比率へ解決する（唯一の解決規則）。

    規約（ISSUE-260 以前から ``handle_market_profile`` が持っていた規則をそのまま単一化）:
      - None・非数値文字列・bool・NaN/Inf は :data:`VA_PCT_DEFAULT`。
      - 数値は ``[VA_PCT_MIN, VA_PCT_MAX]`` へクランプする（例外化せず 500 にしない）。
    """
    if isinstance(raw, bool) or raw is None:
        return VA_PCT_DEFAULT
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            return VA_PCT_DEFAULT
    else:
        return VA_PCT_DEFAULT
    if not _math.isfinite(value):
        return VA_PCT_DEFAULT
    return min(VA_PCT_MAX, max(VA_PCT_MIN, value))


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
    candles, n_bins=60, *, va_pct, want_today=False, want_sessions=False
) -> dict:
    """足ベース TPO マーケットプロファイルを計算する（純関数・副作用なし）。

    Args:
        candles: OHLC 辞書リスト [{"time","open","high","low","close"}, ...]（time 昇順）。
        n_bins: 価格ビン分割数。
        va_pct: バリューエリア比率（必須・キーワード専用）。ISSUE-260: 既定値を持たせない
            （既定は :data:`VA_PCT_DEFAULT` 唯一源が持ち、呼出側が :func:`resolve_va_pct` で
            解決した値を明示的に渡す＝経路ごとに別の既定へ黙って落ちる事故を構造的に断つ）。
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
    """バリューエリアの下限/上限中心価格を返す（**標準 Market Profile** の連続拡張・ISSUE-271）。

    POC（重み最大のビン。同値は index 昇順で決定論化）を起点に、**隣接する側のうち重みが大きい方**へ
    1 ビンずつ広げ、累積が総重み×``va_pct`` に達したところで止める。返すのはその連続区間の
    下端/上端の中心価格。

    なぜ連続拡張か（ISSUE-271）: かつて本関数は「重み降順に**非連続**でビンを積み、採用集合の
    中心価格の min/max」を返していた。これは飛び地を含む集合の外接範囲であり、標準 MP の VA
    （POC を核とする連続した価格帯）ではない。同じ ``va_low``/``va_high`` フィールドを
    ``tf_period_profile._value_area_sparse``（標準 MP）でも算出していたため、**src によって
    同じフィールドの意味が変わる**状態だった（実測: jp225_tick 1h の占有 5 レベル以上 3,000 列で
    79.2% が不一致・VA 幅の中央値 200.0 → 160.0）。定義を標準 MP 側へ統一する。

    重みは float で累積する（ISSUE-085）。``int()`` へ切り捨てると zp の z 値（大半が 1 未満）が
    全て 0 に潰れ、閾値へ届かず VA が全域へ広がる。
    """
    n = len(tpo)
    if n == 0:
        return 0, 0
    total = float(sum(float(t) for t in tpo))
    if total <= 0:
        return float(centers[0]), float(centers[n - 1])
    # POC: 重み最大（同値は index 昇順＝価格の低い側を採り決定論化）。
    poc = min(range(n), key=lambda i: (-float(tpo[i]), i))
    threshold = total * va_pct
    lo = hi = poc
    acc = float(tpo[poc])
    while acc < threshold and (lo > 0 or hi < n - 1):
        down = float(tpo[lo - 1]) if lo > 0 else float("-inf")
        up = float(tpo[hi + 1]) if hi < n - 1 else float("-inf")
        if up >= down:                      # 同値は上側優先（_value_area_sparse と同規約）
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += down
    return float(centers[lo]), float(centers[hi])


# POC/VA 単一定義の公開名（ISSUE-091 A7: tools/gen_js_parity_golden 等の外部利用者は公開 API を
# 参照する。パッケージ内の既存参照（_value_area）は互換のため温存）。
value_area = _value_area


def _bin_index(price, price_min, span, n_bins) -> int:
    """price が属するビン index を返す（[0, n_bins-1] にクランプ）。"""
    idx = int((price - price_min) / span * n_bins)
    if idx < 0:
        return 0
    if idx >= n_bins:
        return n_bins - 1
    return idx
