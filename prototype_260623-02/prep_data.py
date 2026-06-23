#!/usr/bin/env python3
"""Combined prototype data prep: マルチビュー(260621) × OOSホワイトチェック(260623)。

IS / OOS の2区間それぞれにマルチビュー完全 payload（bars/trades/agg/report/meta）を生成し、
加えて区間比較の劣化指標(degradation)・判定(verdict)を出力する。READ ONLY on sources。
出力 prototype_260623-02/data.json を index.html が消費する。使い捨て試作品質。
"""
import bisect
import json
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

ROOT = Path("/workspaces/app")
CONF = ROOT / "simulator/tests/confirmation/2026-04_stop-probe_oos"
OUT = ROOT / "prototype_260623-02/data.json"

POINT_VALUE_JPY = 0.1   # TP=500pts->50 JPY ; SL=200pts->20 JPY => 1pt=0.1 JPY
INITIAL = 10000.0
WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# (key, label, bars_csv, oracle_xlsx, bars_start_filter[YYYY-MM-DD or None])
SEGMENTS = [
    ("is",  "IS（学習 04.01-14）", CONF / "bars_m1_is.csv",
     CONF / "ReportTester-900005560_2604_03.xlsx", None),
    ("oos", "OOS（検証 04.15-23）", CONF / "bars_m1.csv",
     CONF / "ReportTester-900005560_forword_01.xlsx", "2026-04-14"),
]


def parse_dt(s: str) -> int:
    dt = datetime.strptime(str(s).strip()[:19], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fnum(x):
    try:
        return float(str(x).replace(" ", ""))
    except (TypeError, ValueError):
        return None


def read_bars(csv: Path, start_filter=None):
    start_ts = None
    if start_filter:
        start_ts = int(datetime.strptime(start_filter, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    bars = []
    with open(csv) as f:
        next(f)  # header
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            t = parse_dt(f"{p[0]} {p[1]}")
            if start_ts is not None and t < start_ts:
                continue
            bars.append({"time": t, "open": float(p[2]), "high": float(p[3]),
                         "low": float(p[4]), "close": float(p[5])})
    bars.sort(key=lambda b: b["time"])
    return bars


def session_of(h):
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 13:
        return "Europe"
    return "USA"


def excursion(bars, bar_times, side, ep, t0, t1):
    lo = bisect.bisect_left(bar_times, t0)
    hi = bisect.bisect_right(bar_times, t1)
    if hi <= lo:
        return 0.0, 0.0
    seg = bars[lo:hi]
    hh = max(b["high"] for b in seg)
    ll = min(b["low"] for b in seg)
    if side == "buy":
        mfe_pts = max(0.0, hh - ep); mae_pts = max(0.0, ep - ll)
    else:
        mfe_pts = max(0.0, ep - ll); mae_pts = max(0.0, hh - ep)
    return mfe_pts * POINT_VALUE_JPY, mae_pts * POINT_VALUE_JPY


def build_segment(key, label, bars_csv, xlsx, bars_start):
    bars = read_bars(bars_csv, bars_start)
    bar_times = [b["time"] for b in bars]

    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb.active
    rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]

    def cell(r, i):
        return r[i] if i < len(r) and r[i] is not None else None

    # report metrics (label: value pairs)
    report = {}
    for r in rows[:60]:
        flat = list(r)
        for j, c in enumerate(flat):
            if isinstance(c, str) and c.strip().endswith(":"):
                kk = c.strip().rstrip(":")
                for k in range(j + 1, len(flat)):
                    if flat[k] is not None:
                        report[kk] = flat[k]
                        break
    report = {k: str(v) for k, v in report.items()}

    orders_hdr = deals_hdr = None
    for i, r in enumerate(rows):
        v0 = cell(r, 0)
        if v0 == "Orders":
            orders_hdr = i + 1
        elif v0 == "Deals":
            deals_hdr = i + 1

    order_by_id = {}
    orders_raw = []
    for r in rows[orders_hdr + 1:(deals_hdr - 2 if deals_hdr else len(rows))]:
        if cell(r, 0) is None:
            continue
        if cell(r, 0) == "Deals":
            break
        rec = {"open_time": cell(r, 0), "order": cell(r, 1), "symbol": cell(r, 2),
               "type": cell(r, 3), "volume": cell(r, 4), "price": cell(r, 5),
               "sl": cell(r, 6), "tp": cell(r, 7), "time": cell(r, 8),
               "state": cell(r, 9), "comment": cell(r, 10)}
        orders_raw.append(rec)
        try:
            order_by_id[int(rec["order"])] = rec
        except (TypeError, ValueError):
            pass

    deals = []
    for r in rows[deals_hdr + 1:]:
        if cell(r, 0) is None:
            continue
        deals.append({"time": cell(r, 0), "deal": cell(r, 1), "type": cell(r, 3),
                      "dir": cell(r, 4), "volume": cell(r, 5), "price": cell(r, 6),
                      "order": cell(r, 7), "profit": cell(r, 10), "balance": cell(r, 11),
                      "comment": cell(r, 12)})

    # FIFO pairing in->out
    open_stack = []
    trades = []
    tid = 0
    for d in deals:
        if d["dir"] == "in":
            open_stack.append(d)
        elif d["dir"] == "out" and open_stack:
            o = open_stack.pop(0)
            tid += 1
            et = parse_dt(o["time"]); xt = parse_dt(d["time"])
            ep = fnum(o["price"]); xp = fnum(d["price"])
            side = "buy" if str(o["type"]).lower() == "buy" else "sell"
            oid = None
            try:
                oid = int(o["order"])
            except (TypeError, ValueError):
                pass
            ref = order_by_id.get(oid, {})
            trades.append({"id": tid, "side": side, "entry_time": et, "exit_time": xt,
                           "entry_price": ep, "exit_price": xp, "profit": fnum(d["profit"]),
                           "volume": str(o["volume"]), "sl": str(ref.get("sl", "")),
                           "tp": str(ref.get("tp", "")), "order": oid, "comment": d["comment"],
                           "balance": fnum(d["balance"]), "hold_sec": xt - et})

    for t in trades:
        if t["entry_price"] is None:
            t["mfe"] = t["mae"] = 0.0
            continue
        mfe, mae = excursion(bars, bar_times, t["side"], t["entry_price"], t["entry_time"], t["exit_time"])
        t["mfe"] = round(mfe, 2); t["mae"] = round(mae, 2)

    # aggregations
    entries_hour = {h: 0 for h in range(24)}
    entries_session = {"Asia": 0, "Europe": 0, "USA": 0}
    entries_wday = {w: 0 for w in WEEK}
    entries_month = {}
    pl_hour = {h: 0.0 for h in range(24)}
    pl_wday = {w: 0.0 for w in WEEK}
    pl_month = {}
    heat = {}
    for t in trades:
        edt = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc)
        xdt = datetime.fromtimestamp(t["exit_time"], tz=timezone.utc)
        eh, ew, em = edt.hour, WEEK[edt.weekday()], edt.strftime("%Y-%m")
        entries_hour[eh] += 1
        entries_session[session_of(eh)] += 1
        entries_wday[ew] += 1
        entries_month[em] = entries_month.get(em, 0) + 1
        pr = t["profit"] or 0.0
        xh, xw, xm = xdt.hour, WEEK[xdt.weekday()], xdt.strftime("%Y-%m")
        pl_hour[xh] += pr
        pl_wday[xw] += pr
        pl_month[xm] = pl_month.get(xm, 0.0) + pr
        ckey = f"{ew}|{eh}"
        cc = heat.setdefault(ckey, {"profit": 0.0, "count": 0})
        cc["profit"] += pr
        cc["count"] += 1

    HBUCK = [(0, 60, "<1m"), (60, 120, "1-2m"), (120, 300, "2-5m"), (300, 600, "5-10m"),
             (600, 1800, "10-30m"), (1800, 3600, "30-60m"), (3600, 10**9, ">1h")]
    hold_pl = {b[2]: 0.0 for b in HBUCK}
    hold_cnt = {b[2]: 0 for b in HBUCK}
    for t in trades:
        for lo, hi, lab in HBUCK:
            if lo <= t["hold_sec"] < hi:
                hold_pl[lab] += t["profit"] or 0.0
                hold_cnt[lab] += 1
                break

    balance_curve = [{"time": t["exit_time"], "value": t["balance"]}
                     for t in trades if t["balance"] is not None]
    scat_mfe = [{"x": t["mfe"], "y": t["profit"] or 0.0, "id": t["id"]} for t in trades]
    scat_mae = [{"x": t["mae"], "y": t["profit"] or 0.0, "id": t["id"]} for t in trades]
    heat_cells = [{"wday": k.split("|")[0], "hour": int(k.split("|")[1]),
                   "profit": round(v["profit"], 1), "count": v["count"]}
                  for k, v in heat.items()]

    agg = {"entries_hour": entries_hour, "entries_session": entries_session,
           "entries_wday": entries_wday, "entries_month": entries_month,
           "pl_hour": {h: round(v, 1) for h, v in pl_hour.items()},
           "pl_wday": {w: round(v, 1) for w, v in pl_wday.items()},
           "pl_month": {m: round(v, 1) for m, v in pl_month.items()},
           "balance_curve": balance_curve, "scatter_mfe": scat_mfe, "scatter_mae": scat_mae,
           "hold_pl": hold_pl, "hold_cnt": hold_cnt, "weekorder": WEEK, "heat": heat_cells}

    meta = {"symbol": "JP225", "timeframe": "M1",
            "strategy": report.get("Expert", "StopEntryProbe_EA"),
            "bars": len(bars), "trades": len(trades),
            "period": report.get("Period", "")}

    seg = {"label": label, "meta": meta, "report": report, "bars": bars,
           "trades": trades, "orders": orders_raw, "agg": agg}
    print(f"{key}: bars={len(bars)} trades={len(trades)} "
          f"net={round(sum((t['profit'] or 0) for t in trades),1)} "
          f"bal={balance_curve[-1]['value'] if balance_curve else None}")
    return seg


def summarize(seg):
    tr = seg["trades"]
    n = len(tr)
    wins = [t for t in tr if (t["profit"] or 0) > 0]
    loss = [t for t in tr if (t["profit"] or 0) < 0]
    gp = sum(t["profit"] for t in wins)
    gl = sum(t["profit"] for t in loss)
    net = sum((t["profit"] or 0) for t in tr)
    curve = seg["agg"]["balance_curve"]
    peak = curve[0]["value"] if curve else INITIAL
    mdd = 0.0; mdd_pct = 0.0
    for p in curve:
        peak = max(peak, p["value"])
        d = p["value"] - peak
        if d < mdd:
            mdd = d; mdd_pct = (d / peak * 100) if peak else 0.0
    avg_win = gp / len(wins) if wins else 0.0
    avg_loss = gl / len(loss) if loss else 0.0
    return {
        "trades": n, "net": round(net, 1),
        "final_balance": round(curve[-1]["value"], 1) if curve else INITIAL,
        "win_rate": round(len(wins) / n * 100, 2) if n else 0.0,
        "profit_factor": round(gp / abs(gl), 3) if gl else float("inf"),
        "expectancy": round(net / n, 2) if n else 0.0,
        "payoff": round((avg_win / abs(avg_loss)), 3) if avg_loss else float("inf"),
        "return_pct": round((curve[-1]["value"] - INITIAL) / INITIAL * 100, 2) if curve else 0.0,
        "max_dd_pct": round(mdd_pct, 2),
    }


segments = {}
for key, label, bars_csv, xlsx, bars_start in SEGMENTS:
    segments[key] = build_segment(key, label, bars_csv, xlsx, bars_start)

S = {k: summarize(v) for k, v in segments.items()}
IS, OOS = S["is"], S["oos"]


def ratio(o, i):
    return None if i == 0 else round(o / i, 3)


def delta(o, i):
    return round(o - i, 2)


KEYS = ["net", "profit_factor", "win_rate", "expectancy", "payoff", "return_pct", "max_dd_pct"]
degradation = {k: {"is": IS[k], "oos": OOS[k], "ratio": ratio(OOS[k], IS[k]), "delta": delta(OOS[k], IS[k])}
               for k in KEYS}

reasons = []
if IS["net"] > 0 and OOS["net"] <= 0:
    verdict = "fail"
    reasons.append(f"IS黒字(+{IS['net']:.0f})に対しOOS赤字({OOS['net']:.0f})＝未知区間で優位性消失")
elif OOS["profit_factor"] < 1.0:
    verdict = "fail"
    reasons.append(f"OOS PF={OOS['profit_factor']:.3f}<1.0＝検証区間で損失超過")
elif degradation["profit_factor"]["ratio"] is not None and degradation["profit_factor"]["ratio"] < 0.7:
    verdict = "warn"
    reasons.append(f"PF劣化 比={degradation['profit_factor']['ratio']}（OOS/IS<0.7）")
else:
    verdict = "pass"
    reasons.append("OOSでも優位性を維持")
if degradation["win_rate"]["delta"] < -5:
    reasons.append(f"勝率差={degradation['win_rate']['delta']}pt 悪化")
if degradation["expectancy"]["ratio"] is not None and degradation["expectancy"]["ratio"] < 0:
    reasons.append("期待値が正→負へ反転")

data = {
    "meta": {"symbol": "JP225", "timeframe": "M1", "strategy": "StopEntryProbe_EA",
             "params": "ProbeDir=2(両建て) / offset100 / Lot0.1 / SL200 / TP500",
             "initial_deposit": INITIAL, "split": "2026-04-15",
             "note": "IS/OOS 単純分割（同一パラメータを両区間で評価・最適化なし）"},
    "segments": segments,
    "summary": S,
    "degradation": degradation,
    "verdict": {"result": verdict, "reasons": reasons},
}

OUT.write_text(json.dumps(data, separators=(",", ":")))
print(f"WROTE {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")
print(f"VERDICT: {verdict} :: " + " / ".join(reasons))
