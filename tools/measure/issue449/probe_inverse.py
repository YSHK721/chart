"""オシレータ水準 → 価格 の逆写像を実測で確かめる（ISSUE-449 第 1 表へのラダー反映）。

参照実装（各指標の core）を**無改変で前進評価**し、次を測る:
  A. 現在バーの終値 C を動かしたときの指標値 v(C) が区分メビウス（(aC+b)/(C+d)）か
     — 3 点から係数を決めて残りの探針で残差を測る。成り立てば逆写像は閉形式で厳密。
  B. v(C) が単調か（＝到達判定が価格の交差と同値になる条件）
  C. 全水準を価格へ変換したときの本数と、現在値からの距離
  D. ma_marod の「静的投影（今の錨を固定）」と「自己整合解」の差
"""
import json, os, sys, time
import urllib.request

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

_MM = module_loader.load_module("_mm", Path(ROOT + "/indigators/ma_marod/src/core.py"))
_BM = module_loader.load_module("_bm", Path(ROOT + "/indigators/btlm_trail_marod/src/core.py"))
from src.core import compute_rsi_full  # profit_rsi

LEVELS = {
    "ma_marod": ["ma_marod_q5", "ma_marod_q95", "ma_marod_evq_med_hi",
                 "ma_marod_evq_med_lo", "ma_marod_evq_ext_hi", "ma_marod_evq_ext_lo"],
    "btlm_trail_marod": ["btlm_trail_marod_q5", "btlm_trail_marod_q95",
                         "btlm_trail_marod_evq_med_hi", "btlm_trail_marod_evq_med_lo",
                         "btlm_trail_marod_evq_ext_hi", "btlm_trail_marod_evq_ext_lo"],
    "profit_rsi": ["rsi_q10", "rsi_q90", "rsi_evq_ext_hi", "rsi_evq_ext_lo",
                   "rsi_gpd_hi", "rsi_gpd_lo"],
}
LIM = {"1m": 3000, "5m": 3000, "15m": 3000, "1h": 2000,
       "4h": 2000, "1D": 2000, "1W": 1500, "1M": 800}
NPROBE = 600          # 前進評価に使う直近バー数（warm-up 十分）


def get(url, timeout=600):
    with urllib.request.urlopen(BASE + url, timeout=timeout) as r:
        return json.loads(r.read())


def post(body):
    req = urllib.request.Request(BASE + "/compute", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        return json.loads(r.read())


# --- 素材 -------------------------------------------------------------------
candles = {tf: get(f"/candles?datasetRef={REF}&timeframe={tf}&limit={LIM[tf]}")["candles"]
           for tf in TFS}
now = float(candles["1m"][-1]["close"])

raw = json.load(open(D + "export.json"))
tpls = json.loads(raw["templates"])["templates"]
binds = json.loads(raw["bindings"])["bindings"]
byid = {t["templateId"]: t for t in tpls}
cat = json.loads(urllib.request.urlopen(BASE + "/catalog", timeout=30).read())["paramScopes"]

insts = []
for tf in TFS:
    for inst in byid[binds[tf]]["instances"]:
        ind = inst["indicatorId"]
        if ind not in LEVELS:
            continue
        allow = set(cat.get(ind, {}).get(inst.get("variant", "default")) or [])
        p = {k: v for k, v in inst["params"].items() if k in allow}
        own = p.pop("timeframe", None) or "chart"
        axis = own if own != "chart" else tf
        res = post({"indicatorId": ind, "variant": inst.get("variant", "default"),
                    "params": p, "datasetRef": REF, "generation": 0,
                    "timeframe": axis, "limit": LIM[axis], "mode": "full"})
        ser = {}
        for s in res.get("series") or []:
            ser[s["name"]] = {int(d["time"]): d["value"]
                              for d in (s.get("data") or []) if d.get("value") is not None}
        insts.append({"tf": tf, "axis": axis, "ind": ind, "p": p, "ser": ser})
print(f"現在値（1m 最終終値） = {now:,.1f} / instance {len(insts)} 本\n")


# --- 前進評価器（参照実装を無改変で呼ぶ。現在バーの終値だけ差し替える） ------
def make_forward(it):
    cs = candles[it["axis"]][-NPROBE:]
    o = np.array([float(c["open"]) for c in cs])
    h = np.array([float(c["high"]) for c in cs])
    lo = np.array([float(c["low"]) for c in cs])
    cl = np.array([float(c["close"]) for c in cs])
    H0, L0 = h[-1], lo[-1]
    p = it["p"]
    ind = it["ind"]

    def forward(C):
        hh, ll, cc = h.copy(), lo.copy(), cl.copy()
        cc[-1] = C
        hh[-1] = max(H0, C)      # 形成中バーの走行極値（C が越えれば高値も動く）
        ll[-1] = min(L0, C)
        if ind == "ma_marod":
            df = pd.DataFrame({"open": o, "high": hh, "low": ll, "close": cc})
            return float(_MM.ma_marod_series(
                df, source=p["source"], ma_type=p["ma_type"], length=int(p["length"]))[-1])
        if ind == "btlm_trail_marod":
            df = pd.DataFrame({"open": o, "high": hh, "low": ll, "close": cc})
            return float(_BM.marod_series(df, source=p["source"],
                                          maxbars=int(p["maxbars"]))[-1])
        r = compute_rsi_full(o, hh, ll, cc,
                             rsi_period=int(p.get("rsi_period", 6)),
                             apply=int(p.get("apply", 5)))
        return float(np.asarray(r.rsi)[-1])

    return forward, H0, L0


def fit_mobius(pts):
    """v = (aC+b)/(C+d) を 3 点から決める。"""
    A = np.array([[C, 1.0, -v] for C, v in pts])
    y = np.array([v * C for C, v in pts])
    return np.linalg.solve(A, y)          # a, b, d


def invert_mobius(coef, v):
    a, b, d = coef
    return (b - v * d) / (v - a)


# --- A/B. 区分メビウス性と単調性 --------------------------------------------
print("=== A/B. v(C) の形（区分の内側で 3 点からメビウスを決め、他の探針で残差） ===")
print(f"{'足':4s} {'指標':17s} {'区分':10s} {'探針':>4s} {'残差最大':>12s} {'単調':>5s}")
verify = []
for it in insts:
    fwd, H0, L0 = make_forward(it)
    span = max(H0 - L0, 1.0)
    regions = {"C<L": np.linspace(L0 - 3 * span, L0 - 0.05 * span, 9),
               "L<=C<=H": np.linspace(L0 + 0.02 * span, H0 - 0.02 * span, 9),
               "C>H": np.linspace(H0 + 0.05 * span, H0 + 3 * span, 9)}
    allpts = []
    for rname, grid in regions.items():
        pts = [(float(C), fwd(float(C))) for C in grid]
        allpts += pts
        coef = fit_mobius([pts[0], pts[4], pts[8]])
        resid = max(abs(v - (coef[0] * C + coef[1]) / (C + coef[2])) for C, v in pts)
        it.setdefault("coef", {})[rname] = (coef.tolist(), float(grid[0]), float(grid[-1]))
        verify.append(resid)
        print(f"{it['tf']:4s} {it['ind']:17s} {rname:10s} {len(pts):4d} {resid:12.3e}", end="")
        vs = [v for _, v in pts]
        print(f" {'増' if all(b >= a for a, b in zip(vs, vs[1:])) else '×':>5s}")
    it["allpts"] = allpts

print(f"\nメビウス残差の全体最大 = {max(verify):.3e}")

# --- C/D. 水準を価格へ変換 ---------------------------------------------------
print("\n=== C. 水準 → 価格（現在バーで解いた値） ===")
print(f"{'足':4s} {'指標':17s} {'水準':24s} {'水準値':>9s} {'価格':>11s} {'現在値差':>10s} {'区分':>8s}")
rows = []
for it in insts:
    t = max(it["ser"].get(list(LEVELS[it["ind"]])[0], {}).keys() or [0])
    fwd, H0, L0 = make_forward(it)
    for name in LEVELS[it["ind"]]:
        d = it["ser"].get(name, {})
        if not d:
            continue
        tt = max(d.keys())
        v = float(d[tt])
        sol, reg = None, "—"
        for rname, (coef, c0, c1) in it["coef"].items():
            C = invert_mobius(np.array(coef), v)
            # その区分の定義域に入る解だけ採る（区分の内点判定は L0/H0 で行う）
            ok = ((rname == "C<L" and C < L0) or (rname == "L<=C<=H" and L0 <= C <= H0)
                  or (rname == "C>H" and C > H0))
            if ok and np.isfinite(C):
                sol, reg = float(C), rname
                break
        if sol is None:
            print(f"{it['tf']:4s} {it['ind']:17s} {name:24s} {v:9.3f} "
                  f"{'解なし':>11s}")
            rows.append({"tf": it["tf"], "ind": it["ind"], "name": name,
                         "v": v, "price": None, "region": None})
            continue
        # 参照実装で往復検算（逆算した価格を前進評価へ入れて水準に戻るか）
        back = fwd(sol)
        rows.append({"tf": it["tf"], "ind": it["ind"], "name": name, "v": v,
                     "price": sol, "region": reg, "back": back, "err": abs(back - v)})
        print(f"{it['tf']:4s} {it['ind']:17s} {name:24s} {v:9.3f} "
              f"{sol:11.1f} {sol-now:+10.1f} {reg:>8s}")

json.dump(rows, open(D + "inverse_rows.json", "w"))

ok = [r for r in rows if r.get("price") is not None]
errs = sorted(r["err"] for r in ok)
dists = sorted(abs(r["price"] - now) for r in ok)
print(f"\n水準 {len(rows)} 本 / 解あり {len(ok)} 本 / 解なし {len(rows)-len(ok)} 本")
print(f"往復検算の誤差（水準の単位）: 中央 {errs[len(errs)//2]:.3e} / 最大 {errs[-1]:.3e}")
print(f"現在値からの距離（点）: 中央 {dists[len(dists)//2]:.0f} / "
      f"最小 {dists[0]:.0f} / 最大 {dists[-1]:.0f}")
for lim in (350, 1000, 5000):
    print(f"  |距離| <= {lim:5d} 点 : {sum(1 for d in dists if d <= lim):3d} 本")

# D. ma_marod の静的投影との差
print("\n=== D. ma_marod: 静的投影（錨を固定）と自己整合解の差 ===")
diffs = []
for it in insts:
    if it["ind"] != "ma_marod":
        continue
    cs = candles[it["axis"]]
    c = cs[-1]
    x = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
    tt = max(it["ser"]["ma_marod"].keys())
    ma_t = x / (1.0 + float(it["ser"]["ma_marod"][tt]) / 100.0)
    for r in rows:
        if r["ind"] != "ma_marod" or r["tf"] != it["tf"] or r["price"] is None:
            continue
        x_naive = (1.0 + r["v"] / 100.0) * ma_t          # 静的投影（source 空間）
        diffs.append((it["tf"], int(it["p"]["length"]), r["name"], x_naive, r["price"],
                      x_naive - r["price"]))
for tf, L, name, xn, pr, dd in diffs:
    print(f"{tf:4s} len={L:3d} {name:22s} 静的 {xn:10.1f} / 自己整合 {pr:10.1f} / 差 {dd:+9.1f}")
ad = sorted(abs(d[-1]) for d in diffs)
print(f"\n差（点）: 中央 {ad[len(ad)//2]:.1f} / 最大 {ad[-1]:.1f}")
