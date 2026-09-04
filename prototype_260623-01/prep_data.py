#!/usr/bin/env python3
"""OOS white-check prototype data prep (READ ONLY on sources).

IS/OOS の 2 オラクル xlsx を読み、区間別の主要指標・残高曲線・劣化指標・判定を
data.json に書き出す。consumed by index.html。使い捨て試作品質。
No source file is modified.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

ROOT = Path("/workspaces/app")
CONF = ROOT / "simulator/tests/confirmation/2026-04_stop-probe_oos"
BARS = CONF / "bars_m1.csv"   # 全期間 OHLC（03-23〜04-29・IS/OOS 両区間を含む）
OUT = ROOT / "prototype_260623-01/data.json"

INITIAL = 10000.0

# 区間定義: (キー, ラベル, オラクル xlsx, MT5 期待値[trades, net, balance])
SEGMENTS = [
    ("is",  "IS（学習区間 04.01-14）", "ReportTester-900005560_2604_03.xlsx", 5224, 11370.0, 21370.0),
    ("oos", "OOS（検証区間 04.15-23）", "ReportTester-900005560_forword_01.xlsx", 2438, -4020.0, 5980.0),
]


def parse_dt(s: str) -> int:
    dt = datetime.strptime(str(s).strip()[:19], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fnum(x):
    try:
        return float(str(x).replace(" ", ""))
    except (TypeError, ValueError):
        return None


def read_bars(csv: Path):
    """全期間 OHLC（tab区切り・<DATE>/<TIME>/<OPEN>/<HIGH>/<LOW>/<CLOSE>...）を読む。"""
    bars = []
    with open(csv) as f:
        next(f)  # header
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            t = parse_dt(f"{p[0]} {p[1]}")
            bars.append({"time": t, "open": float(p[2]), "high": float(p[3]),
                         "low": float(p[4]), "close": float(p[5])})
    bars.sort(key=lambda b: b["time"])
    return bars


def read_deals(xlsx: Path):
    """xlsx Deals セクションを読み、in→out のラウンドトリップに対を組む。"""
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb.active
    rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
    deals_hdr = None
    for i, r in enumerate(rows):
        if r and str(r[0]).strip() == "Deals":
            deals_hdr = i + 1
            break
    if deals_hdr is None:
        raise RuntimeError(f"Deals section not found in {xlsx.name}")
    hdr = [str(c).strip() if c is not None else "" for c in rows[deals_hdr]]
    col = {name: idx for idx, name in enumerate(hdr)}

    def get(r, name):
        i = col.get(name)
        return r[i] if i is not None and i < len(r) else None

    # FIFO ペアリング（両建て=同足 buy+sell の連続 in に耐えるため単一スロットでなくスタック）
    trades = []
    open_stack = []
    tid = 0
    for r in rows[deals_hdr + 1:]:
        if not r or r[0] is None:
            continue
        direction = str(get(r, "Direction") or "").strip()
        if direction not in ("in", "out"):
            continue
        if direction == "in":
            open_stack.append(r)
        elif direction == "out" and open_stack:
            o = open_stack.pop(0)
            tid += 1
            et = parse_dt(get(o, "Time"))
            xt = parse_dt(get(r, "Time"))
            side = "buy" if str(get(o, "Type")).strip().lower() == "buy" else "sell"
            trades.append({
                "id": tid, "side": side,
                "entry_time": et, "exit_time": xt,
                "entry_price": fnum(get(o, "Price")),
                "exit_price": fnum(get(r, "Price")),
                "profit": fnum(get(r, "Profit")),
                "balance": fnum(get(r, "Balance")),
                "hold_sec": xt - et,
            })
    return trades


def stats_of(trades):
    n = len(trades)
    if n == 0:
        return {}
    wins = [t for t in trades if (t["profit"] or 0) > 0]
    loss = [t for t in trades if (t["profit"] or 0) < 0]
    gp = sum(t["profit"] for t in wins)
    gl = sum(t["profit"] for t in loss)   # negative
    net = sum((t["profit"] or 0) for t in trades)
    avg_win = gp / len(wins) if wins else 0.0
    avg_loss = gl / len(loss) if loss else 0.0
    holds = [t["hold_sec"] for t in trades]
    # balance curve + max drawdown (残高ベース)
    curve = [{"time": t["exit_time"], "value": t["balance"]}
             for t in trades if t["balance"] is not None]
    peak = curve[0]["value"] if curve else INITIAL
    mdd = 0.0
    mdd_pct = 0.0
    for p in curve:
        if p["value"] > peak:
            peak = p["value"]
        d = p["value"] - peak
        if d < mdd:
            mdd = d
            mdd_pct = (d / peak * 100) if peak else 0.0
    return {
        "trades": n,
        "wins": len(wins), "losses": len(loss),
        "win_rate": len(wins) / n * 100,
        "gross_profit": round(gp, 1), "gross_loss": round(gl, 1),
        "net": round(net, 1),
        "final_balance": round(curve[-1]["value"], 1) if curve else INITIAL,
        "profit_factor": (gp / abs(gl)) if gl else float("inf"),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "payoff": (avg_win / abs(avg_loss)) if avg_loss else float("inf"),
        "expectancy": round(net / n, 2),
        "avg_hold_sec": round(sum(holds) / n, 0),
        "max_dd": round(mdd, 1), "max_dd_pct": round(mdd_pct, 2),
        "return_pct": round((curve[-1]["value"] - INITIAL) / INITIAL * 100, 2) if curve else 0.0,
        "balance_curve": curve,
    }


bars = read_bars(BARS)
print(f"bars: {len(bars)} ({bars[0]['time']}..{bars[-1]['time']})")

segs = {}
markers = {}   # 区間別の建玉マーカー（建値時刻・side・勝敗）
for key, label, fname, exp_trades, exp_net, exp_bal in SEGMENTS:
    trades = read_deals(CONF / fname)
    markers[key] = [{"t": t["entry_time"], "xt": t["exit_time"],
                     "s": 1 if t["side"] == "buy" else 0,
                     "w": 1 if (t["profit"] or 0) > 0 else 0,
                     "p": t["profit"]} for t in trades]
    s = stats_of(trades)
    s["label"] = label
    s["expected"] = {"trades": exp_trades, "net": exp_net, "balance": exp_bal}
    s["oracle_match"] = (
        s["trades"] == exp_trades
        and abs(s["net"] - exp_net) < 0.5
        and abs(s["final_balance"] - exp_bal) < 0.5
    )
    segs[key] = s
    print(f"{key}: trades={s['trades']} net={s['net']} bal={s['final_balance']} "
          f"PF={s['profit_factor']:.3f} win={s['win_rate']:.1f}% match={s['oracle_match']}")

IS, OOS = segs["is"], segs["oos"]


def ratio(o, i):
    if i == 0:
        return None
    return round(o / i, 3)


def delta(o, i):
    return round(o - i, 2)


# 劣化指標（OOS vs IS）。比 ratio と差分 Δ の両建て。
degradation = {
    "net":            {"is": IS["net"], "oos": OOS["net"], "ratio": ratio(OOS["net"], IS["net"]), "delta": delta(OOS["net"], IS["net"])},
    "profit_factor":  {"is": round(IS["profit_factor"], 3), "oos": round(OOS["profit_factor"], 3), "ratio": ratio(OOS["profit_factor"], IS["profit_factor"]), "delta": delta(OOS["profit_factor"], IS["profit_factor"])},
    "win_rate":       {"is": round(IS["win_rate"], 2), "oos": round(OOS["win_rate"], 2), "ratio": ratio(OOS["win_rate"], IS["win_rate"]), "delta": delta(OOS["win_rate"], IS["win_rate"])},
    "expectancy":     {"is": IS["expectancy"], "oos": OOS["expectancy"], "ratio": ratio(OOS["expectancy"], IS["expectancy"]), "delta": delta(OOS["expectancy"], IS["expectancy"])},
    "payoff":         {"is": round(IS["payoff"], 3), "oos": round(OOS["payoff"], 3), "ratio": ratio(OOS["payoff"], IS["payoff"]), "delta": delta(OOS["payoff"], IS["payoff"])},
    "return_pct":     {"is": IS["return_pct"], "oos": OOS["return_pct"], "ratio": ratio(OOS["return_pct"], IS["return_pct"]), "delta": delta(OOS["return_pct"], IS["return_pct"])},
    "max_dd_pct":     {"is": IS["max_dd_pct"], "oos": OOS["max_dd_pct"], "ratio": ratio(OOS["max_dd_pct"], IS["max_dd_pct"]), "delta": delta(OOS["max_dd_pct"], IS["max_dd_pct"])},
}

# 過剰最適化の判定（ホワイトチェック）。
# 規則: OOSが赤字かつISが黒字 → 過剰最適化シグナル(赤)。PF劣化が大 → 警告(黄)。
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
    reasons.append(f"勝率Δ={degradation['win_rate']['delta']}pt 悪化")
if degradation["expectancy"]["ratio"] is not None and degradation["expectancy"]["ratio"] < 0:
    reasons.append("期待値が正→負へ反転")

data = {
    "meta": {
        "symbol": "JP225", "timeframe": "M1",
        "strategy": "StopEntryProbe_EA",
        "params": "ProbeDir=2(両建て) / offset100 / Lot0.1 / SL200 / TP500",
        "initial_deposit": INITIAL,
        "split": "2026-04-15",
        "note": "IS/OOS 単純分割（同一パラメータを両区間で評価・最適化なし）",
    },
    "segments": segs,
    "degradation": degradation,
    "verdict": {"result": verdict, "reasons": reasons},
    "bars": bars,
    "markers": markers,
}

OUT.write_text(json.dumps(data, separators=(",", ":")))
print(f"WROTE {OUT} ({OUT.stat().st_size/1e3:.1f} KB)")
print(f"VERDICT: {verdict} :: " + " / ".join(reasons))
