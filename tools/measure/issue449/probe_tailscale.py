"""M5. 裾の目盛り — 帯外を GPD で解像できるかを実測する（ISSUE-454 の未測定 1）。

現状（経験順位のみ）は正常帯を超えたバーの 3〜30% が p=1.0 に張り付き、
「わずかに超えた」と「極端」を区別できない（M3）。ここでは帯外の目盛りを
**既存の POT/GPD** で与える案を測る。参照実装 `common.gpd` を無改変で使う。

    帯内 : p = 経験順位（当該バー除外の因果ローリング分位）           ∈ [0, q_high]
    帯外 : p = q_high + (1 - q_high) * F_GPD(v - u ; xi, beta)        ∈ (q_high, 1]

GPD は既存規約どおり **エピソード極値へ畳んだ超過分の直近 k_events 件**へ当てはめる
（`common.event_quantiles.step_events` の event_agg="episode"・tickvol
`step_excess_event` と同一）。観測 30 件（`common.gpd.MIN_GPD_EVENTS`）未満は当てはめない。

測るもの:
  M5-a 飽和の解消 : 帯超バーのうち p=1.0 に張り付く割合（現状 3〜30%）
  M5-b 分解能     : 帯超バーの中で p が何段階に分かれるか（経験順位 対 GPD）
  M5-c 終端超過   : xi<0 は有限終端を持つ。v がその終端を超える割合（＝1.0 へ張り付く）
  M5-d 当てはめ不能: 観測 30 件未満で GPD を出せないバーの割合（代替の要否）
  M5-e 単調・連続 : 目盛りが v について単調か、接合点 v=u で跳ばないか
"""
import json, os, sys
from pathlib import Path

import numpy as np
import urllib.request

ROOT = os.environ.get("ISSUE449_ROOT", str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, ROOT)
from common import gpd as _gpd
from common import event_quantiles as _evq

D = os.environ.get("ISSUE449_DIR", os.path.dirname(os.path.abspath(__file__))) + "/"
BASE = os.environ.get("ISSUE449_API", "http://127.0.0.1:8001")
REF = "jp225_tick"
TFS = ["1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M"]
OSC = ["ma_marod", "btlm_trail_marod", "profit_rsi", "tickvol"]
VALUE = {"ma_marod": "ma_marod", "btlm_trail_marod": "btlm_trail_marod",
         "profit_rsi": "rsi", "tickvol": "tickvol"}
HI = {"ma_marod": "ma_marod_q95", "btlm_trail_marod": "btlm_trail_marod_q95",
      "profit_rsi": "rsi_q90", "tickvol": "tickvol_q90"}
QHI = {"ma_marod": 0.95, "btlm_trail_marod": 0.95, "profit_rsi": 0.90, "tickvol": 0.90}
LIM = {"1m": 20000, "5m": 15000, "15m": 6000, "1h": 2500,
       "4h": 2000, "1D": 2000, "1W": 1500, "1M": 800}
K_EVENTS = 50


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


def excess_of(ind, v, u):
    """各指標の超過分の定義（参照実装に合わせる）。"""
    if ind == "profit_rsi":
        return (v - u) / (100.0 - u) if (100.0 - u) > 0 else np.nan   # levels.py ③
    return v - u                                                      # tickvol と同形


def causal_rank(v, window_n):
    n = v.size
    out = np.full(n, np.nan)
    for t in range(n):
        w = v[max(0, t - window_n):t]
        w = w[np.isfinite(w)]
        if w.size >= 2 and np.isfinite(v[t]):
            out[t] = float(np.count_nonzero(w < v[t])) / w.size
    return out


print(f"{'足':4s} {'指標':18s} {'帯超':>6s} | {'経験 p=1':>8s} {'GPD p=1':>8s} | "
      f"{'経験 相異':>8s} {'GPD 相異':>8s} | {'終端超':>7s} {'当て不能':>8s} | {'xi 中央':>8s} {'接合の跳び':>9s}")
print("-" * 118)
summary = []
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
                ser[s["name"]] = {int(x["time"]): float(x["value"]) for x in d}
        if VALUE[ind] not in ser or HI[ind] not in ser:
            continue
        times = sorted(ser[VALUE[ind]])
        v = np.array([ser[VALUE[ind]][t] for t in times])
        u = np.array([ser[HI[ind]].get(t, np.nan) for t in times])
        wn = int(p.get("window_n", 500))
        q_hi = QHI[ind]
        emp = causal_rank(v, wn)

        # エピソード畳み込み（既存規約）で超過分の観測列を作りながら前進する
        up, run_up = [], []
        gpd_p, xis, endpoint_over, nofit, over_idx = [], [], 0, 0, []
        cache = None
        for i in range(v.size):
            ui, vi = u[i], v[i]
            if np.isfinite(ui) and np.isfinite(vi) and vi > ui:
                over_idx.append(i)
                e = excess_of(ind, vi, ui)
                window = np.asarray(up[-K_EVENTS:], dtype=np.float64)
                window = window[np.isfinite(window)]
                if window.size < _gpd.MIN_GPD_EVENTS:
                    nofit += 1
                    gpd_p.append(np.nan)
                else:
                    key = (window.size, float(window[-1]), float(window[0]))
                    if cache is None or cache[0] != key:
                        f = _gpd.gpd_fit(window)
                        cache = (key, f)
                    f = cache[1]
                    xis.append(f.xi)
                    if f.xi < 0:
                        end = -f.beta / f.xi          # 有限終端
                        if e >= end:
                            endpoint_over += 1
                    cdf = float(np.asarray(_gpd.gpd_cdf(np.array([e]), f.xi, f.beta))[0])
                    gpd_p.append(q_hi + (1.0 - q_hi) * cdf)
            # 観測列の更新（当該バーの確定後）
            if np.isfinite(ui) and np.isfinite(vi):
                _evq.step_events(excess_of(ind, vi, ui), float("-inf"), 0.0,
                                 "episode", up, [], run_up, [])
        if not over_idx:
            continue
        gp = np.array(gpd_p)
        ep = emp[np.array(over_idx)]
        ep = ep[np.isfinite(ep)]
        gpf = gp[np.isfinite(gp)]
        # 接合の跳び: 帯直上のバーで GPD 目盛りが q_hi からどれだけ離れるか
        jump = np.nanmin(gpf) - q_hi if gpf.size else np.nan
        summary.append((tf, ind, len(over_idx)))
        print(f"{tf:4s} {ind:18s} {len(over_idx):6d} | "
              f"{np.count_nonzero(ep >= 1.0)/max(ep.size,1)*100:7.1f}% "
              f"{np.count_nonzero(gpf >= 1.0)/max(gpf.size,1)*100:7.1f}% | "
              f"{np.unique(ep).size:8d} {np.unique(np.round(gpf,6)).size:8d} | "
              f"{endpoint_over/max(len(over_idx),1)*100:6.1f}% "
              f"{nofit/max(len(over_idx),1)*100:7.1f}% | "
              f"{np.median(xis) if xis else float('nan'):8.3f} {jump:9.4f}")

print("\nM5-e 単調性の確認: 目盛りは v について単調か")
print("  帯内 = 経験順位（v の非減少関数）・帯外 = q_hi + (1-q_hi)*F_GPD(v-u)")
print("  F_GPD は超過分について単調増加なので、u を固定すれば全域で単調。")
print("  接合点は上表『接合の跳び』（GPD 側の最小値 − q_hi）で確認する。")
