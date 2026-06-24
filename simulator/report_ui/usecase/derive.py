"""決定論導出 純関数群（report_ui ステージ①・詳細設計 §4.2/§6.2）。

domain のみ依存・pandas非依存・int時刻のみを扱う（usecase→domain 依存方向を保つ）。
sl/tp 導出・excursion(mfe/mae)・session/hold バケット・balance再構成・max DD% を提供する。
"""
from __future__ import annotations

import bisect
from datetime import datetime, timezone

# wday インデックス規約（R-2・アーキ指針 §4）: weekday() Mon=0..Sun=6（UTC 基準）。
# front 側 `(getUTCDay()+6)%7` と単一規約で一致させる（heat 分類とフィルタ判定が同一 trade を選ぶ）。
WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

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


def heat_cells(entries):
    """entry 時刻×損益を曜日×時間セルへ集計する（§4.7 agg.heat・試作 prep_data.py:185-223）。

    entries は (entry_time:int, profit:float) の反復子。entry の UTC wday|hour で
    グルーピングし、各セルに profit 合計・count・wins(profit>0) を積む。
    時刻分解は datetime.fromtimestamp(int_ts, tz=timezone.utc) で決定論（domain依存・int時刻のみ）。
    返り値は [{wday,hour,profit,count,wins}]（heat セル列）。空入力は空列。
    """
    heat = {}
    for entry_time, profit in entries:
        dt = datetime.fromtimestamp(int(entry_time), tz=timezone.utc)
        wday = WEEK[dt.weekday()]
        hour = dt.hour
        cell = heat.setdefault((wday, hour), {"profit": 0.0, "count": 0, "wins": 0})
        cell["profit"] += profit
        cell["count"] += 1
        if profit > 0:
            cell["wins"] += 1
    return [
        {"wday": w, "hour": h, "profit": round(v["profit"], 1),
         "count": v["count"], "wins": v["wins"]}
        for (w, h), v in heat.items()
    ]


def entries_buckets(entry_times):
    """entry_time(int) 列を hour/session/wday/month 件数へ集計する（§4・試作 prep_data.py:177-201）。

    基準は entry_time の UTC（entries 系は entry 基準）。hour(0..23)/session/wday は 0 埋めで
    全キー確保、month は出現分のみ。session は session_of(UTC hour) を再利用する。
    時刻分解は datetime.fromtimestamp(int_ts, tz=timezone.utc) で決定論（domain依存・int時刻のみ）。
    """
    hour = {h: 0 for h in range(24)}
    session = {"Asia": 0, "Europe": 0, "USA": 0}
    wday = {w: 0 for w in WEEK}
    month = {}
    for et in entry_times:
        dt = datetime.fromtimestamp(int(et), tz=timezone.utc)
        h = dt.hour
        hour[h] += 1
        session[session_of(h)] += 1
        wday[WEEK[dt.weekday()]] += 1
        m = dt.strftime("%Y-%m")
        month[m] = month.get(m, 0) + 1
    return {"hour": hour, "session": session, "wday": wday, "month": month}


def pl_buckets(items):
    """(exit_time:int, profit) 列を hour/wday/month 損益へ集計する（§4・試作 prep_data.py:182-202）。

    基準は exit_time の UTC（pl 系は exit 基準）。hour(0..23)/wday は 0 埋めで全キー確保、month は
    出現分のみ。各値は試作同様 round(合算, 1)。
    """
    hour = {h: 0.0 for h in range(24)}
    wday = {w: 0.0 for w in WEEK}
    month = {}
    for xt, profit in items:
        dt = datetime.fromtimestamp(int(xt), tz=timezone.utc)
        pr = profit or 0.0
        hour[dt.hour] += pr
        wday[WEEK[dt.weekday()]] += pr
        m = dt.strftime("%Y-%m")
        month[m] = month.get(m, 0.0) + pr
    return {
        "hour": {h: round(v, 1) for h, v in hour.items()},
        "wday": {w: round(v, 1) for w, v in wday.items()},
        "month": {m: round(v, 1) for m, v in month.items()},
    }


def scatter_points(items):
    """(x, profit, id) 列を散布点 [{x, y, id}] へ写す（§4・試作 prep_data.py:218-219）。

    x=mfe または mae（呼び出し側が選択）、y=profit、id=trade id。空入力は空列。
    """
    return [{"x": x, "y": (profit or 0.0), "id": tid} for x, profit, tid in items]


def hold_buckets(items):
    """(hold_sec, profit) 列を hold_bucket 7 区分の損益/件数へ集計する（§4・試作 prep_data.py:206-216）。

    バケット境界は hold_bucket(hold_sec) を再利用（[lo,hi) 半開区間）。pl/cnt とも全 7 ラベルを
    0 埋めで確保する。
    """
    labels = [lab for _, _, lab in _HBUCK]
    pl = {lab: 0.0 for lab in labels}
    cnt = {lab: 0 for lab in labels}
    for sec, profit in items:
        lab = hold_bucket(int(sec))
        pl[lab] += profit or 0.0
        cnt[lab] += 1
    return {"pl": pl, "cnt": cnt}


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
