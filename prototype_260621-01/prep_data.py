#!/usr/bin/env python3
"""Prototype data prep: bars + MT5 report xlsx -> data.json (READ ONLY on sources).

Outputs prototype_260621-01/data.json consumed by index.html.
No source file is modified. Throwaway prototype quality.
"""
import json, re, zipfile, bisect
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/workspaces/app")
CONF = ROOT / "simulator/tests/confirmation/2026-04_stop-probe_oos"
BARS = CONF / "bars_m1_is.csv"          # IS bars (03-23 .. 04-14) matching this report
XLSX = CONF / "ReportTester-900005560_2604_03.xlsx"  # IS oracle report
OUT  = ROOT / "prototype_260621-01/data.json"

POINT_VALUE_JPY = 0.1   # TP=500pts -> 50 JPY ; SL=200pts -> 20 JPY  => 1pt = 0.1 JPY


def parse_dt(s: str) -> int:
    # "2026.03.23 01:00:00" -> epoch seconds (treat as UTC)
    dt = datetime.strptime(s.strip(), "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# ---------- 1. bars ----------
bars = []
with open(BARS) as f:
    next(f)  # header
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) < 6:
            continue
        t = parse_dt(f"{p[0]} {p[1]}")
        bars.append({"time": t, "open": float(p[2]), "high": float(p[3]),
                     "low": float(p[4]), "close": float(p[5])})
bars.sort(key=lambda b: b["time"])
bar_times = [b["time"] for b in bars]
print(f"bars: {len(bars)}")


# ---------- 2. read xlsx (openpyxl) ----------
import openpyxl
wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
ws = wb.active
rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]


def cell(r, i):
    return r[i] if i < len(r) and r[i] is not None else None


# ---- 2a. report metrics (rows 0..~39) key:value pairs ----
report = {}
for r in rows[:60]:
    vals = [c for c in r if c is not None]
    # pattern: label ending ':' followed by value, possibly multiple per row
    i = 0
    flat = list(r)
    for j, c in enumerate(flat):
        if isinstance(c, str) and c.strip().endswith(":"):
            key = c.strip().rstrip(":")
            # next non-None
            for k in range(j + 1, len(flat)):
                if flat[k] is not None:
                    report[key] = flat[k]
                    break
report = {k: (str(v)) for k, v in report.items()}

# ---- 2b. locate sections ----
orders_hdr = deals_hdr = None
for i, r in enumerate(rows):
    v0 = cell(r, 0)
    if v0 == "Orders":
        orders_hdr = i + 1
    elif v0 == "Deals":
        deals_hdr = i + 1
print(f"orders_hdr={orders_hdr} deals_hdr={deals_hdr}")

# ---- 2c. Orders table (raw, for reference / SL-TP lookup by order id) ----
order_by_id = {}
orders_raw = []
oh = rows[orders_hdr]  # header
for r in rows[orders_hdr + 1:deals_hdr - 2 if deals_hdr else len(rows)]:
    if cell(r, 0) is None:
        continue
    if cell(r, 0) == "Deals":
        break
    rec = {
        "open_time": cell(r, 0), "order": cell(r, 1), "symbol": cell(r, 2),
        "type": cell(r, 3), "volume": cell(r, 4), "price": cell(r, 5),
        "sl": cell(r, 6), "tp": cell(r, 7), "time": cell(r, 8),
        "state": cell(r, 9), "comment": cell(r, 10),
    }
    orders_raw.append(rec)
    try:
        order_by_id[int(rec["order"])] = rec
    except (TypeError, ValueError):
        pass
print(f"orders: {len(orders_raw)}")

# ---- 2d. Deals -> pair in/out into round-trip trades ----
deals = []
for r in rows[deals_hdr + 1:]:
    if cell(r, 0) is None:
        continue
    deals.append({
        "time": cell(r, 0), "deal": cell(r, 1), "symbol": cell(r, 2),
        "type": cell(r, 3), "dir": cell(r, 4), "volume": cell(r, 5),
        "price": cell(r, 6), "order": cell(r, 7), "commission": cell(r, 8),
        "swap": cell(r, 9), "profit": cell(r, 10), "balance": cell(r, 11),
        "comment": cell(r, 12),
    })
print(f"deals: {len(deals)}")


def fnum(x):
    try:
        return float(str(x).replace(" ", ""))
    except (TypeError, ValueError):
        return None


# FIFO pairing of in -> out (single-symbol netting, sequential)
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
        trades.append({
            "id": tid, "side": side,
            "entry_time": et, "exit_time": xt,
            "entry_price": ep, "exit_price": xp,
            "profit": fnum(d["profit"]),
            "volume": str(o["volume"]),
            "sl": str(ref.get("sl", "")), "tp": str(ref.get("tp", "")),
            "order": oid, "comment": d["comment"],
            "balance": fnum(d["balance"]),
            "hold_sec": xt - et,
        })
print(f"trades: {len(trades)}")


# ---- 2e. MFE / MAE per trade from bars (JPY) ----
def excursion(side, ep, t0, t1):
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


for t in trades:
    if t["entry_price"] is None:
        t["mfe"] = t["mae"] = 0.0
        continue
    mfe, mae = excursion(t["side"], t["entry_price"], t["entry_time"], t["exit_time"])
    t["mfe"] = round(mfe, 2); t["mae"] = round(mae, 2)


# ---------- 3. aggregations ----------
WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def session_of(h):
    # rough FX sessions (UTC): Asia 0-7, Europe 7-13, USA 13-22
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 13:
        return "Europe"
    return "USA"


entries_hour = {h: 0 for h in range(24)}
entries_session = {"Asia": 0, "Europe": 0, "USA": 0}
entries_wday = {w: 0 for w in WEEK}
entries_month = {}
pl_hour = {h: 0.0 for h in range(24)}
pl_wday = {w: 0.0 for w in WEEK}
pl_month = {}
heat = {}  # (wday, hour) -> {profit, count}

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
    key = f"{ew}|{eh}"
    cell_ = heat.setdefault(key, {"profit": 0.0, "count": 0})
    cell_["profit"] += pr
    cell_["count"] += 1

# holding-time buckets (P/L by holding time)
HBUCK = [(0, 60, "<1m"), (60, 120, "1-2m"), (120, 300, "2-5m"),
         (300, 600, "5-10m"), (600, 1800, "10-30m"), (1800, 3600, "30-60m"),
         (3600, 10**9, ">1h")]
hold_pl = {b[2]: 0.0 for b in HBUCK}
hold_cnt = {b[2]: 0 for b in HBUCK}
for t in trades:
    for lo, hi, lab in HBUCK:
        if lo <= t["hold_sec"] < hi:
            hold_pl[lab] += t["profit"] or 0.0
            hold_cnt[lab] += 1
            break

# balance curve (running balance at each closed trade) + equity proxy
balance_curve = [{"time": t["exit_time"], "value": t["balance"]}
                 for t in trades if t["balance"] is not None]

# correlation scatter
scat_mfe = [{"x": t["mfe"], "y": t["profit"] or 0.0, "id": t["id"]} for t in trades]
scat_mae = [{"x": t["mae"], "y": t["profit"] or 0.0, "id": t["id"]} for t in trades]

heat_cells = [{"wday": k.split("|")[0], "hour": int(k.split("|")[1]),
               "profit": round(v["profit"], 1), "count": v["count"]}
              for k, v in heat.items()]

data = {
    "meta": {
        "symbol": "JP225", "timeframe": "M1",
        "strategy": report.get("Expert", "StopEntryProbe_EA"),
        "bars": len(bars), "trades": len(trades),
        "period": report.get("Period", ""),
    },
    "report": report,
    "bars": bars,
    "trades": trades,
    "orders": orders_raw,
    "agg": {
        "entries_hour": entries_hour,
        "entries_session": entries_session,
        "entries_wday": entries_wday,
        "entries_month": entries_month,
        "pl_hour": {h: round(v, 1) for h, v in pl_hour.items()},
        "pl_wday": {w: round(v, 1) for w, v in pl_wday.items()},
        "pl_month": {m: round(v, 1) for m, v in pl_month.items()},
        "balance_curve": balance_curve,
        "scatter_mfe": scat_mfe,
        "scatter_mae": scat_mae,
        "hold_pl": hold_pl,
        "hold_cnt": hold_cnt,
        "weekorder": WEEK,
        "heat": heat_cells,
    },
}

OUT.write_text(json.dumps(data, separators=(",", ":")))
print(f"WROTE {OUT}  ({OUT.stat().st_size/1e6:.1f} MB)")
print("sanity: net profit from trades =", round(sum((t['profit'] or 0) for t in trades), 1),
      " expected 11370")
print("final balance =", trades[-1]['balance'] if trades else None, " expected 21370")
