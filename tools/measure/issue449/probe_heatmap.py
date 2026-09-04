"""案 B（価格ラダーの各行を分位で塗る）の成立性を実測する。

前段（probe_inverse.py）で v(C) が区分メビウスと確認できた。ここでは:
  1. RSI の分岐点（x_t が x_{t-1} を跨ぐ点）を区分に加え、全区分で残差を機械精度まで落とす
  2. **1 instance あたり 3 回の前進評価だけ**で係数を決め、その閉形式で
     第 1 表の価格 88 本すべてを評価する
  3. その結果が「価格 1 本ずつ参照実装を直接呼んだ値」と一致するか（＝近似でないこと）
  4. 前進評価の発行回数がラダー行数に依存しないこと（計算量の表明）
"""
import json, os, sys
import urllib.request
from pathlib import Path

ROOT = os.environ.get("ISSUE449_ROOT", str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/indigators")
sys.path.insert(0, ROOT + "/indigators/profit_rsi")

import numpy as np
import pandas as pd
from common import module_loader

D = os.environ.get("ISSUE449_DIR",
                   os.path.dirname(os.path.abspath(__file__))) + "/"
BASE = os.environ.get("ISSUE449_API", "http://127.0.0.1:8001")
REF = "jp225_tick"
TFS = ["1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M"]
PRICE_IND = {"moving_averages", "btlm_trail", "cvfe"}
NPROBE = 600
LIM = {"1m": 3000, "5m": 3000, "15m": 3000, "1h": 2000,
       "4h": 2000, "1D": 2000, "1W": 1500, "1M": 800}

_MM = module_loader.load_module("_mm", Path(ROOT + "/indigators/ma_marod/src/core.py"))
_BM = module_loader.load_module("_bm", Path(ROOT + "/indigators/btlm_trail_marod/src/core.py"))
from src.core import compute_rsi_full

OSC = {"ma_marod", "btlm_trail_marod", "profit_rsi"}
LEVELS = {
    "ma_marod": ["ma_marod_q5", "ma_marod_evq_med_lo", "ma_marod_evq_ext_lo",
                 "ma_marod_q95", "ma_marod_evq_med_hi", "ma_marod_evq_ext_hi"],
    "btlm_trail_marod": ["btlm_trail_marod_q5", "btlm_trail_marod_evq_med_lo",
                         "btlm_trail_marod_evq_ext_lo", "btlm_trail_marod_q95",
                         "btlm_trail_marod_evq_med_hi", "btlm_trail_marod_evq_ext_hi"],
    "profit_rsi": ["rsi_q10", "rsi_evq_ext_lo", "rsi_gpd_lo",
                   "rsi_q90", "rsi_evq_ext_hi", "rsi_gpd_hi"],
}


def get(url, timeout=600):
    with urllib.request.urlopen(BASE + url, timeout=timeout) as r:
        return json.loads(r.read())


def post(body):
    req = urllib.request.Request(BASE + "/compute", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        return json.loads(r.read())


candles = {tf: get(f"/candles?datasetRef={REF}&timeframe={tf}&limit={LIM[tf]}")["candles"]
           for tf in TFS}
now = float(candles["1m"][-1]["close"])

raw = json.load(open(D + "export.json"))
tpls = json.loads(raw["templates"])["templates"]
binds = json.loads(raw["bindings"])["bindings"]
byid = {t["templateId"]: t for t in tpls}
cat = json.loads(urllib.request.urlopen(BASE + "/catalog", timeout=30).read())["paramScopes"]

# --- 第 1 表の価格水準（最終バーの値だけ）＝ラダーの行 ----------------------
ladder, seen = [], set()
osc_insts = []
for tf in TFS:
    for inst in byid[binds[tf]]["instances"]:
        ind = inst["indicatorId"]
        if ind not in PRICE_IND and ind not in OSC:
            continue
        allow = set(cat.get(ind, {}).get(inst.get("variant", "default")) or [])
        p = {k: v for k, v in inst["params"].items() if k in allow}
        own = p.pop("timeframe", None) or "chart"
        axis = own if own != "chart" else tf
        key = (ind, json.dumps(p, sort_keys=True), axis)
        if key in seen:
            continue
        seen.add(key)
        lim = LIM[axis]
        if ind == "cvfe":
            lim = max(lim, int(p.get("n_har", 0)) + 400)
        try:
            res = post({"indicatorId": ind, "variant": inst.get("variant", "default"),
                        "params": p, "datasetRef": REF, "generation": 0,
                        "timeframe": axis, "limit": lim, "mode": "full"})
        except urllib.error.HTTPError as e:
            print(f"  !! {axis} {ind}: {e.code} {e.read()[:200]}")
            continue
        ser = {}
        for s in res.get("series") or []:
            data = [d for d in (s.get("data") or []) if d.get("value") is not None]
            if data:
                ser[s["name"]] = data
        if ind in PRICE_IND:
            for name, data in ser.items():
                vals = sorted(float(d["value"]) for d in data)
                if not (10000 < vals[len(vals) // 2] < 200000):
                    continue        # 価格スケールでない系列は捨てる（§11 の規約）
                label = (f"MA {p['ma_type']}{p['length']} {p['source']}"
                         if ind == "moving_averages" else name)
                ladder.append({"tf": axis, "label": label,
                               "price": float(data[-1]["value"])})
        else:
            osc_insts.append({"tf": tf, "axis": axis, "ind": ind, "p": p,
                              "ser": {n: d for n, d in ser.items()}})

ladder.sort(key=lambda r: -r["price"])
print(f"現在値 = {now:,.1f} / ラダー行 {len(ladder)} 本 / オシレータ instance {len(osc_insts)} 本\n")


# --- 前進評価器（発行回数を数える Test Spy 付き） ---------------------------
class Forward:
    calls = 0

    def __init__(self, it):
        cs = candles[it["axis"]][-NPROBE:]
        self.o = np.array([float(c["open"]) for c in cs])
        self.h = np.array([float(c["high"]) for c in cs])
        self.l = np.array([float(c["low"]) for c in cs])
        self.c = np.array([float(c["close"]) for c in cs])
        self.H0, self.L0 = self.h[-1], self.l[-1]
        self.p, self.ind = it["p"], it["ind"]
        # 一つ前のバーの適用価格（RSI の上下分岐の境目）
        self.x_prev = (self.h[-2] + self.l[-2] + self.c[-2]) / 3.0

    def __call__(self, C):
        Forward.calls += 1
        hh, ll, cc = self.h.copy(), self.l.copy(), self.c.copy()
        cc[-1] = C
        hh[-1] = max(self.H0, C)
        ll[-1] = min(self.L0, C)
        p = self.p
        if self.ind == "ma_marod":
            df = pd.DataFrame({"open": self.o, "high": hh, "low": ll, "close": cc})
            return float(_MM.ma_marod_series(df, source=p["source"],
                                             ma_type=p["ma_type"],
                                             length=int(p["length"]))[-1])
        if self.ind == "btlm_trail_marod":
            df = pd.DataFrame({"open": self.o, "high": hh, "low": ll, "close": cc})
            return float(_BM.marod_series(df, source=p["source"],
                                          maxbars=int(p["maxbars"]))[-1])
        r = compute_rsi_full(self.o, hh, ll, cc,
                             rsi_period=int(p.get("rsi_period", 6)),
                             apply=int(p.get("apply", 5)))
        return float(np.asarray(r.rsi)[-1])

    def breakpoints(self):
        """区分の境目。H/L（適用価格 hlc3 の折れ）＋ RSI の上下分岐。"""
        bp = [self.L0, self.H0]
        if self.ind == "profit_rsi":
            x = self.x_prev
            for cand in (3 * x - self.H0 - self.L0,          # L<=C<=H の枝
                         (3 * x - self.L0) / 2.0,            # C>H の枝
                         (3 * x - self.H0) / 2.0):           # C<L の枝
                if np.isfinite(cand):
                    bp.append(cand)
        return sorted(set(round(b, 9) for b in bp))


def fit_mobius(pts):
    A = np.array([[C, 1.0, -v] for C, v in pts])
    y = np.array([v * C for C, v in pts])
    return np.linalg.solve(A, y)


def ev(coef, C):
    a, b, d = coef
    return (a * C + b) / (C + d)


# --- 係数決定（1 区分 3 回）→ 88 行を閉形式で評価 ---------------------------
print("=== 1. 区分メビウスの当てはめ残差（RSI の分岐点を区分へ加えた後） ===")
print(f"{'足':4s} {'指標':17s} {'区分数':>5s} {'発行':>5s} {'残差最大':>11s}")
for it in osc_insts:
    f = Forward(it)
    bps = f.breakpoints()
    edges = [-np.inf] + bps + [np.inf]
    it["f"], it["pieces"] = f, []
    lo_span = max(f.H0 - f.L0, 1.0)
    n0 = Forward.calls
    worst = 0.0
    for a, b in zip(edges, edges[1:]):
        aa = a if np.isfinite(a) else b - 4 * lo_span
        bb = b if np.isfinite(b) else a + 4 * lo_span
        if bb - aa < 1e-6:
            continue
        g = [aa + (bb - aa) * t for t in (0.15, 0.5, 0.85)]
        pts = [(C, f(C)) for C in g]
        coef = fit_mobius(pts)
        it["pieces"].append({"lo": a, "hi": b, "coef": coef})
        # 検算用の追加探針（この探針は「発行回数」の主張には数えず別勘定）
        chk = [aa + (bb - aa) * t for t in (0.3, 0.7)]
        for C in chk:
            worst = max(worst, abs(f(C) - ev(coef, C)))
    print(f"{it['tf']:4s} {it['ind']:17s} {len(it['pieces']):5d} "
          f"{Forward.calls-n0:5d} {worst:11.3e}")

print("\n=== 2. ラダー 88 行の評価: 閉形式 と 参照実装の直接呼び出しの一致 ===")
Forward.calls = 0
fast = {}
for it in osc_insts:
    vals = []
    for row in ladder:
        C = row["price"]
        pc = next(p for p in it["pieces"] if p["lo"] <= C <= p["hi"])
        vals.append(ev(pc["coef"], C))
    fast[id(it)] = vals
issued_fast = Forward.calls

Forward.calls = 0
worst = 0.0
for it in osc_insts:
    for i, row in enumerate(ladder):
        worst = max(worst, abs(it["f"](row["price"]) - fast[id(it)][i]))
issued_direct = Forward.calls
print(f"最大差 = {worst:.3e}")
print(f"前進評価の発行回数: 閉形式 {issued_fast} 回 / 直接 {issued_direct} 回")
print(f"係数決定に要した発行回数 = instance ごとに 区分数 x 3 "
      f"（ラダー行数に依存しない）")

# --- 3. 各行の分位段（ヒートマップの中身） ---------------------------------
def stage(ind, v, lv):
    """水準の並びから段を決める（設計書 §5.3 の順序）。"""
    hi = [lv.get(n) for n in LEVELS[ind][3:]]
    lo = [lv.get(n) for n in LEVELS[ind][:3]]
    q_hi, med_hi, ext_hi = hi
    q_lo, med_lo, ext_lo = lo
    if ext_hi is not None and v >= ext_hi: return +3
    if med_hi is not None and v >= med_hi: return +2
    if q_hi is not None and v >= q_hi:     return +1
    if ext_lo is not None and v <= ext_lo: return -3
    if med_lo is not None and v <= med_lo: return -2
    if q_lo is not None and v <= q_lo:     return -1
    return 0

for it in osc_insts:
    tt = max(max(d and [int(x["time"]) for x in d] or [0])
             for d in it["ser"].values())
    it["lv"] = {}
    for n in LEVELS[it["ind"]]:
        d = it["ser"].get(n)
        if d:
            it["lv"][n] = float(d[-1]["value"])

print("\n=== 3. ヒートマップ: ラダー各行で各オシレータが何段になるか ===")
print("（段: -3 GPD/ext 下超 … 0 帯内 … +3 GPD/ext 上超）\n")
hdr = f"{'価格':>10s} {'現差':>8s} {'出所':22s} "
cols = [f"{it['ind'][:6]}/{it['tf']}" for it in osc_insts]
print(hdr + " ".join(f"{c:>12s}" for c in cols[:6]))
counts = {}
for i, row in enumerate(ladder):
    st = [stage(it["ind"], fast[id(it)][i], it["lv"]) for it in osc_insts]
    for s in st:
        counts[s] = counts.get(s, 0) + 1
    if abs(row["price"] - now) <= 60:
        print(f"{row['price']:10.1f} {row['price']-now:+8.1f} "
              f"{(row['tf']+' '+row['label'])[:22]:22s} "
              + " ".join(f"{s:>12d}" for s in st[:6]))

print(f"\n段の分布（ラダー {len(ladder)} 行 x instance {len(osc_insts)} 本 "
      f"= {len(ladder)*len(osc_insts)} セル）")
for s in sorted(counts):
    print(f"  段 {s:+d}: {counts[s]:5d} セル ({counts[s]/(len(ladder)*len(osc_insts))*100:5.1f}%)")

json.dump({"ladder": ladder, "now": now,
           "cells": {f"{it['ind']}@{it['tf']}": fast[id(it)] for it in osc_insts}},
          open(D + "heatmap.json", "w"), ensure_ascii=False)
