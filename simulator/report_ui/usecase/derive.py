"""決定論導出 純関数群（report_ui ステージ①・詳細設計 §4.2/§6.2）。

domain のみ依存・pandas非依存・int時刻のみを扱う（usecase→domain 依存方向を保つ）。
sl/tp 導出・excursion(mfe/mae)・session/hold バケット・balance再構成・max DD% を提供する。
"""
from __future__ import annotations

import bisect

# hold バケット定義（詳細設計 §6.2 HBUCK・試作 prep_data.py:206-207 踏襲）。
_HBUCK = [
    (0, 60, "<1m"),
    (60, 120, "1-2m"),
    (120, 300, "2-5m"),
    (300, 600, "5-10m"),
    (600, 1800, "10-30m"),
    (1800, 3600, "30-60m"),
    (3600, 10 ** 9, ">1h"),
]


def derive_sl_tp(side, entry_price, *, sl_points, tp_points, stops_level, point_size, digits):
    """EA 固定パラメータから entry_price ± 距離で SL/TP を導出する（詳細設計 §4.2.1）。

    距離 = max(points × point_size, stops_level × point_size)。points==0 のとき空文字。
    返り値は digits 桁固定の文字列（xlsx S/L,T/P との桁突合用）。
    """
    min_dist = stops_level * point_size

    def fmt(p):
        return f"{round(p, digits):.{digits}f}"

    sl = ""
    tp = ""
    if sl_points > 0:
        d = max(sl_points * point_size, min_dist)
        sl = fmt(entry_price - d) if side == "buy" else fmt(entry_price + d)
    if tp_points > 0:
        d = max(tp_points * point_size, min_dist)
        tp = fmt(entry_price + d) if side == "buy" else fmt(entry_price - d)
    return sl, tp


def excursion(bars, bar_times, side, ep, t0, t1, point_size):
    """保有区間 [t0, t1] の bars を走査し mfe/mae を JPY 換算で返す（詳細設計 §4.2.3）。

    bars は high/low 属性を持つ要素列、bar_times はそれと同順の int 時刻昇順列。
    区間にバーが無い（hi<=lo）とき (0.0, 0.0)。
    """
    lo = bisect.bisect_left(bar_times, t0)
    hi = bisect.bisect_right(bar_times, t1)
    if hi <= lo:
        return 0.0, 0.0
    seg = bars[lo:hi]
    hh = max(b.high for b in seg)
    ll = min(b.low for b in seg)
    if side == "buy":
        mfe_pts = max(0.0, hh - ep)
        mae_pts = max(0.0, ep - ll)
    else:
        mfe_pts = max(0.0, ep - ll)
        mae_pts = max(0.0, hh - ep)
    return round(mfe_pts * point_size, 2), round(mae_pts * point_size, 2)


def session_of(h):
    """UTC hour をセッション区分へ写す（詳細設計 §6.2・試作踏襲）。"""
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 13:
        return "Europe"
    return "USA"


def hold_bucket(sec):
    """保有秒を HBUCK 7 区分のラベルへ写す（詳細設計 §6.2）。"""
    for lo, hi, lab in _HBUCK:
        if lo <= sec < hi:
            return lab
    return _HBUCK[-1][2]


def reconstruct_balance_curve(exit_times, balances):
    """exit_time と走行残高を 1:1 で [{time,value}] へ再構成する（致命-3・§4.2.5）。

    len 不一致は致命-3 の 1:1 不変条件違反として ValueError を送出する。
    """
    if len(exit_times) != len(balances):
        raise ValueError(
            f"exit_times と balances の長さが不一致（致命-3 1:1 違反）: "
            f"{len(exit_times)} != {len(balances)}"
        )
    return [{"time": t, "value": v} for t, v in zip(exit_times, balances)]


def max_drawdown_pct(curve):
    """balance_curve（[{time,value}]）の peak-to-trough 最大 DD% を返す（§4.8）。"""
    if not curve:
        return 0.0
    peak = curve[0]["value"]
    mdd_pct = 0.0
    for p in curve:
        peak = max(peak, p["value"])
        d = p["value"] - peak
        if peak:
            pct = d / peak * 100
            if pct < mdd_pct:
                mdd_pct = pct
    return round(mdd_pct, 2)
