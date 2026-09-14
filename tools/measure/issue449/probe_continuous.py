"""第 2 表を連続量のヒートマップにできるかを実測する（段ラベル廃止・ISSUE-455 検討）。

候補の連続量は **因果ローリング分位** p_t ∈ [0,1]:
    窓 = values[max(0, t-window_n) : t]（当該バー除外・common.marod_bands と同一規約）
    p_t = 窓内で v_t 未満の割合
既存の水準（q10 / q90 / evq / gpd）はこの p のしきい値にすぎないので、
p は 4 指標・8 時間足を通じて**同一の意味**を持つ唯一の量である。

測るもの:
  M1 飽和   : p が 0 または 1 に張り付く割合（張り付くと色が動かない）
  M2 分布   : p の 10 分割ヒストグラム（一様なら色域を使い切る）
  M3 裾の分解能: q90 超のバーの中で p が何段階に分かれるか
  M4 形成中バーのバイアス（tickvol 固有・決定的）:
      形成途中の足は tick 数が途中までしか積み上がっていない。確定足の分布に当てると
      どれだけ低く出るかを、1m から部分足を再構成して経過割合ごとに測る。
"""
import json, os, sys
import urllib.request
from pathlib import Path

import numpy as np

D = os.environ.get("ISSUE449_DIR", os.path.dirname(os.path.abspath(__file__))) + "/"
BASE = os.environ.get("ISSUE449_API", "http://127.0.0.1:8001")
REF = "jp225_tick"
TFS = ["1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M"]
OSC = {"ma_marod", "btlm_trail_marod", "profit_rsi", "tickvol"}
VALUE = {"ma_marod": "ma_marod", "btlm_trail_marod": "btlm_trail_marod",
         "profit_rsi": "rsi", "tickvol": "tickvol"}
BAND_HI = {"ma_marod": "ma_marod_q95", "btlm_trail_marod": "btlm_trail_marod_q95",
           "profit_rsi": "rsi_q90", "tickvol": "tickvol_q90"}
LIM = {"1m": 50000, "5m": 15000, "15m": 6000, "1h": 2500,
       "4h": 2000, "1D": 2000, "1W": 1500, "1M": 800}
DEFAULT_WINDOW = {"ma_marod": 500, "btlm_trail_marod": 500,
                  "profit_rsi": 500, "tickvol": 500}


def post(body):
    req = urllib.request.Request(BASE + "/compute", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read())


raw = json.load(open(D + "export.json"))
tpls = json.loads(raw["templates"])["templates"]
binds = json.loads(raw["bindings"])["bindings"]
byid = {t["templateId"]: t for t in tpls}
cat = json.loads(urllib.request.urlopen(BASE + "/catalog", timeout=30).read())["paramScopes"]


def causal_pct(v, window_n):
    """当該バー除外の因果ローリング分位（窓内で v_t 未満の割合）。"""
    n = v.size
    out = np.full(n, np.nan)
    for t in range(n):
        w = v[max(0, t - window_n):t]
        w = w[np.isfinite(w)]
        if w.size < 2 or not np.isfinite(v[t]):
            continue
        out[t] = float(np.count_nonzero(w < v[t])) / w.size
    return out


rows = []
for tf in TFS:
    for inst in byid[binds[tf]]["instances"]:
        ind = inst["indicatorId"]
        if ind not in OSC:
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
            d = [x for x in (s.get("data") or []) if x.get("value") is not None]
            if d:
                ser[s["name"]] = (np.array([int(x["time"]) for x in d]),
                                  np.array([float(x["value"]) for x in d]))
        if VALUE[ind] not in ser:
            continue
        t, v = ser[VALUE[ind]]
        wn = int(p.get("window_n", DEFAULT_WINDOW[ind]))
        pct = causal_pct(v, wn)
        rows.append({"tf": tf, "ind": ind, "wn": wn, "t": t, "v": v, "pct": pct,
                     "hi": ser.get(BAND_HI[ind])})
        print(f"  {tf:4s} {ind:18s} window_n={wn:4d} バー {v.size:6d}")

print("\n" + "=" * 78)
print("M1/M2. 因果ローリング分位 p の飽和と分布")
print("=" * 78)
print(f"{'足':4s} {'指標':18s} {'有効':>6s} {'p=0':>7s} {'p=1':>7s} "
      f"{'|10 分割ヒストグラム（%）':<44s}")
for r in rows:
    p = r["pct"][np.isfinite(r["pct"])]
    if p.size == 0:
        continue
    z = np.count_nonzero(p <= 0.0) / p.size * 100
    o = np.count_nonzero(p >= 1.0) / p.size * 100
    h = np.histogram(p, bins=10, range=(0, 1))[0] / p.size * 100
    bar = " ".join(f"{x:4.1f}" for x in h)
    print(f"{r['tf']:4s} {r['ind']:18s} {p.size:6d} {z:6.2f}% {o:6.2f}%  {bar}")

print("\n" + "=" * 78)
print("M3. 裾の分解能（正常帯 上端を超えたバーの中で p が何段階に分かれるか）")
print("=" * 78)
print(f"{'足':4s} {'指標':18s} {'帯超バー':>8s} {'p の相異値':>10s} {'p=1 の割合':>10s} {'最大 p':>8s}")
for r in rows:
    if r["hi"] is None:
        continue
    th, hv = r["hi"]
    idx = {int(x): i for i, x in enumerate(r["t"])}
    sel = [(idx[int(x)], hv[j]) for j, x in enumerate(th) if int(x) in idx]
    over = [r["pct"][i] for i, lv in sel
            if np.isfinite(r["pct"][i]) and r["v"][i] >= lv]
    if not over:
        continue
    over = np.array(over)
    print(f"{r['tf']:4s} {r['ind']:18s} {over.size:8d} {np.unique(over).size:10d} "
          f"{np.count_nonzero(over >= 1.0)/over.size*100:9.1f}% {over.max():8.4f}")
