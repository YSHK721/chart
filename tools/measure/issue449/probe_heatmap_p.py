"""M7. 第 1 表の背景を「段」ではなく連続量 p で塗ったときの分布（§5.5 を §5.3 へ揃える）。

probe_heatmap.py（区分メビウスで各ラダー行の指標値を出す）と
probe_tailscale.py（値 → p の写像: 帯内は経験順位・帯外は GPD）を合わせる。

測るもの:
  1色    : 全 instance のうち p が 0.5 から最も離れたもの（|p-0.5| 最大）の分布
  3分割  : 地平（短期/中期/長期）ごとに同じ規則で選んだ p の分布
  食い違い: 3 地平の p の最大差が行ごとにどれだけあるか
"""
import json, os, sys
from pathlib import Path

import numpy as np
import pandas as pd
import urllib.request

ROOT = os.environ.get("ISSUE449_ROOT", "/workspaces/app")
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/indigators")
sys.path.insert(0, ROOT + "/indigators/profit_rsi")
from common import module_loader, gpd as _gpd, event_quantiles as _evq

D = os.environ.get("ISSUE449_DIR", os.path.dirname(os.path.abspath(__file__))) + "/"
BASE = os.environ.get("ISSUE449_API", "http://127.0.0.1:8001")
REF = "jp225_tick"
TFS = ["1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M"]
PRICE_IND = {"moving_averages", "btlm_trail", "cvfe"}
OSC = ["ma_marod", "btlm_trail_marod", "profit_rsi"]
VALUE = {"ma_marod": "ma_marod", "btlm_trail_marod": "btlm_trail_marod", "profit_rsi": "rsi"}
HI = {"ma_marod": "ma_marod_q95", "btlm_trail_marod": "btlm_trail_marod_q95",
      "profit_rsi": "rsi_q90"}
LO = {"ma_marod": "ma_marod_q5", "btlm_trail_marod": "btlm_trail_marod_q5",
      "profit_rsi": "rsi_q10"}
QHI = {"ma_marod": 0.95, "btlm_trail_marod": 0.95, "profit_rsi": 0.90}
QLO = {"ma_marod": 0.05, "btlm_trail_marod": 0.05, "profit_rsi": 0.10}
LIM = {"1m": 20000, "5m": 20000, "15m": 20000, "1h": 20000,
       "4h": 20000, "1D": 3706, "1W": 742, "1M": 171}
NPROBE, K_EVENTS = 600, 50
SHORT, MID = {"1m", "5m", "15m"}, {"1h", "4h"}

_MM = module_loader.load_module("_mm", Path(ROOT + "/indigators/ma_marod/src/core.py"))
_BM = module_loader.load_module("_bm", Path(ROOT + "/indigators/btlm_trail_marod/src/core.py"))
from src.core import compute_rsi_full


def get(u, t=900):
    with urllib.request.urlopen(BASE + u, timeout=t) as r:
        return json.loads(r.read())


def post(b):
    q = urllib.request.Request(BASE + "/compute", data=json.dumps(b).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(q, timeout=1800) as r:
        return json.loads(r.read())


candles = {tf: get(f"/candles?datasetRef={REF}&timeframe={tf}&limit={LIM[tf]}")["candles"]
           for tf in TFS}
now = float(candles["1m"][-1]["close"])
raw = json.load(open(D + "export.json"))
tpls = json.loads(raw["templates"])["templates"]
binds = json.loads(raw["bindings"])["bindings"]
byid = {t["templateId"]: t for t in tpls}
cat = get("/catalog", 30)["paramScopes"]

ladder, seen, insts = [], set(), []
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
        except urllib.error.HTTPError:
            continue
        ser = {}
        for s in res.get("series") or []:
            d = [x for x in (s.get("data") or []) if x.get("value") is not None]
            if d:
                ser[s["name"]] = d
        if ind in PRICE_IND:
            for name, d in ser.items():
                vals = sorted(float(x["value"]) for x in d)
                if not (10000 < vals[len(vals) // 2] < 200000):
                    continue
                lab = (f"MA {p['ma_type']}{p['length']} {p['source']}"
                       if ind == "moving_averages" else name)
                ladder.append({"tf": axis, "label": lab, "price": float(d[-1]["value"])})
        else:
            insts.append({"tf": tf, "axis": axis, "ind": ind, "p": p, "ser": ser})
ladder.sort(key=lambda r: -r["price"])
print(f"現在値 {now:,.1f} / ラダー {len(ladder)} 行 / オシレータ {len(insts)} 本\n")


def forward_of(it):
    cs = candles[it["axis"]][-NPROBE:]
    o = np.array([float(c["open"]) for c in cs]); h = np.array([float(c["high"]) for c in cs])
    l = np.array([float(c["low"]) for c in cs]); c_ = np.array([float(c["close"]) for c in cs])
    H0, L0 = h[-1], l[-1]; p, ind = it["p"], it["ind"]
    xp = (h[-2] + l[-2] + c_[-2]) / 3.0

    def f(C):
        hh, ll, cc = h.copy(), l.copy(), c_.copy()
        cc[-1] = C; hh[-1] = max(H0, C); ll[-1] = min(L0, C)
        if ind == "ma_marod":
            return float(_MM.ma_marod_series(pd.DataFrame(
                {"open": o, "high": hh, "low": ll, "close": cc}),
                source=p["source"], ma_type=p["ma_type"], length=int(p["length"]))[-1])
        if ind == "btlm_trail_marod":
            return float(_BM.marod_series(pd.DataFrame(
                {"open": o, "high": hh, "low": ll, "close": cc}),
                source=p["source"], maxbars=int(p["maxbars"]))[-1])
        r = compute_rsi_full(o, hh, ll, cc, rsi_period=int(p.get("rsi_period", 6)),
                             apply=int(p.get("apply", 5)))
        return float(np.asarray(r.rsi)[-1])

    bp = [L0, H0]
    if ind == "profit_rsi":
        bp += [3 * xp - H0 - L0, (3 * xp - L0) / 2.0, (3 * xp - H0) / 2.0]
    return f, sorted(set(round(b, 9) for b in bp)), H0, L0


def fit(pts):
    A = np.array([[C, 1.0, -v] for C, v in pts]); y = np.array([v * C for C, v in pts])
    return np.linalg.solve(A, y)


def excess(ind, v, u):
    return (v - u) / (100.0 - u) if ind == "profit_rsi" else v - u


for it in insts:
    f, bps, H0, L0 = forward_of(it)
    edges = [-np.inf] + bps + [np.inf]
    span = max(H0 - L0, 1.0); it["pieces"] = []
    for a, b in zip(edges, edges[1:]):
        aa = a if np.isfinite(a) else b - 4 * span
        bb = b if np.isfinite(b) else a + 4 * span
        if bb - aa < 1e-6:
            continue
        g = [aa + (bb - aa) * t for t in (0.15, 0.5, 0.85)]
        it["pieces"].append({"lo": a, "hi": b, "coef": fit([(C, f(C)) for C in g])})
    # 履歴（p を求める窓と GPD の観測列）
    d = it["ser"][VALUE[it["ind"]]]
    it["hist"] = np.array([float(x["value"]) for x in d])
    hi = {int(x["time"]): float(x["value"]) for x in it["ser"].get(HI[it["ind"]], [])}
    it["u"] = hi.get(int(d[-1]["time"]), np.nan)
    up, run = [], []
    tm = [int(x["time"]) for x in d]
    for t, v in zip(tm, it["hist"]):
        u = hi.get(t)
        if u is not None and np.isfinite(v):
            _evq.step_events(excess(it["ind"], v, u), float("-inf"), 0.0, "episode",
                             up, [], run, [])
    w = np.asarray(up[-K_EVENTS:], dtype=np.float64)
    w = w[np.isfinite(w)]
    it["gpd"] = _gpd.gpd_fit(w) if w.size >= _gpd.MIN_GPD_EVENTS else None
    it["wn"] = int(it["p"].get("window_n", 500))


def p_of(it, v):
    """値 v -> p（帯内=経験順位・帯外=GPD。§5.3 の定義）。"""
    win = it["hist"][-it["wn"]:]
    win = win[np.isfinite(win)]
    if win.size < 2 or not np.isfinite(v):
        return np.nan
    u, q_hi = it["u"], QHI[it["ind"]]
    if np.isfinite(u) and v > u:
        if it["gpd"] is None:
            return np.nan                       # 目盛りなし（§5.3.2 の 7 セル）
        e = excess(it["ind"], v, u)
        cdf = float(np.asarray(_gpd.gpd_cdf(np.array([e]), it["gpd"].xi, it["gpd"].beta))[0])
        return q_hi + (1.0 - q_hi) * cdf
    return float(np.count_nonzero(win < v)) / win.size


def hz(tf):
    return 0 if tf in SHORT else (1 if tf in MID else 2)


rows = []
for i, r in enumerate(ladder):
    per = [[], [], []]
    for it in insts:
        C = r["price"]
        pc = next((x for x in it["pieces"] if x["lo"] <= C <= x["hi"]), None)
        if pc is None:
            continue
        a, b, dd = pc["coef"]
        per[hz(it["tf"])].append(p_of(it, (a * C + b) / (C + dd)))
    sel = []
    for g in per:
        g = [x for x in g if np.isfinite(x)]
        sel.append(max(g, key=lambda x: abs(x - 0.5)) if g else np.nan)
    rows.append(sel)

R = np.array(rows)
one = np.array([max([x for x in r if np.isfinite(x)], key=lambda x: abs(x - 0.5))
                if np.isfinite(r).any() else np.nan for r in R])
NAM = ["短期", "中期", "長期"]
bins = [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001]


def hist(a):
    a = a[np.isfinite(a)]
    h = np.histogram(a, bins=bins)[0]
    return " ".join(f"{x:3d}" for x in h), a.size


print("=== 1 色（全 instance のうち p が 0.5 から最も離れたもの） ===")
h, n = hist(one)
print(f"  p の 10 分割: {h}   有効 {n} 行")
print(f"  p >= 0.9 の行: {np.count_nonzero(one[np.isfinite(one)] >= 0.9)} / {n}")
print(f"  p <= 0.1 の行: {np.count_nonzero(one[np.isfinite(one)] <= 0.1)} / {n}")
print(f"  相異値: {np.unique(np.round(one[np.isfinite(one)], 4)).size}")

print("\n=== 3 分割（地平ごと） ===")
for k in range(3):
    h, n = hist(R[:, k])
    print(f"  {NAM[k]}: {h}   有効 {n} 行 / 相異値 "
          f"{np.unique(np.round(R[np.isfinite(R[:,k]),k],4)).size}")

d = np.nanmax(R, axis=1) - np.nanmin(R, axis=1)
d = d[np.isfinite(d)]
print(f"\n=== 3 地平の p の最大差（行ごと） ===")
print(f"  中央 {np.median(d):.3f} / 平均 {d.mean():.3f} / 最小 {d.min():.3f} / 最大 {d.max():.3f}")
for th in (0.1, 0.3, 0.5):
    print(f"  差 > {th}: {np.count_nonzero(d > th)} / {d.size} 行 "
          f"({np.count_nonzero(d > th)/d.size*100:.0f}%)")

print("\n=== 現在値 ±60 点の行 ===")
print(f"{'価格':>10s} {'現差':>7s} {'出所':20s}  {'短期':>6s} {'中期':>6s} {'長期':>6s} | {'1色':>6s}")
for i, r in enumerate(ladder):
    if abs(r["price"] - now) <= 60:
        s = " ".join(f"{R[i,k]:6.3f}" if np.isfinite(R[i, k]) else f"{'—':>6s}"
                     for k in range(3))
        print(f"{r['price']:10.1f} {r['price']-now:+7.1f} "
              f"{(r['tf']+' '+r['label'])[:20]:20s}  {s} | "
              f"{one[i]:6.3f}" if np.isfinite(one[i]) else "")
