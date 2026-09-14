"""M4. 形成中バーのバイアス（tickvol・決定的）。

tick 数は足の中で積み上がる量なので、**形成途中の足は必ず小さい**。それを確定足の分布へ
当てると、経過割合が小さいうちは常に「極端に静か」と出る。1m の tickvol から上位足の
部分足を再構成し、経過割合 f ごとに因果ローリング分位 p を測る。

比較のため ma_marod（積み上がらない量）でも同じことをする。
"""
import json, os
import urllib.request

import numpy as np

D = os.environ.get("ISSUE449_DIR", os.path.dirname(os.path.abspath(__file__))) + "/"
BASE = os.environ.get("ISSUE449_API", "http://127.0.0.1:8001")
REF = "jp225_tick"
PERIOD = {"5m": 300, "15m": 900, "1h": 3600}
WINDOW = 500
FRACS = [0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0]


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


def params_of(tf, ind):
    for inst in byid[binds[tf]]["instances"]:
        if inst["indicatorId"] != ind:
            continue
        allow = set(cat.get(ind, {}).get(inst.get("variant", "default")) or [])
        p = {k: v for k, v in inst["params"].items() if k in allow}
        p.pop("timeframe", None)
        return p, inst.get("variant", "default")
    return None, None


# 1m の tickvol（部分足の再構成に使う）
p1, v1 = params_of("1m", "tickvol")
res = post({"indicatorId": "tickvol", "variant": v1, "params": p1, "datasetRef": REF,
            "generation": 0, "timeframe": "1m", "limit": 50000, "mode": "full"})
d = next(s for s in res["series"] if s["name"] == "tickvol")["data"]
t1 = np.array([int(x["time"]) for x in d])
q1 = np.array([float(x["value"]) for x in d])
print(f"1m tickvol {q1.size} 本\n")


def causal_pct_against(values_full, probe, window_n):
    """probe[t] を「確定足 values_full の直近 window_n 本（当該バー除外）」へ当てた分位。"""
    n = probe.size
    out = np.full(n, np.nan)
    for t in range(n):
        w = values_full[max(0, t - window_n):t]
        w = w[np.isfinite(w)]
        if w.size < 2 or not np.isfinite(probe[t]):
            continue
        out[t] = float(np.count_nonzero(w < probe[t])) / w.size
    return out


print("=" * 74)
print("M4-a. tickvol: 形成中の足を確定足の分布へ当てたときの分位 p（中央値）")
print("=" * 74)
print(f"{'足':5s} " + " ".join(f"{'f='+format(f,'.1f'):>8s}" for f in FRACS) + "   バー数")
for tf, per in PERIOD.items():
    bucket = (t1 // per) * per
    keys = np.unique(bucket)
    # 完全な足だけを使う（1m が per/60 本そろっているもの）
    full_rows, partial = [], {f: [] for f in FRACS}
    nmin = per // 60
    for k in keys:
        m = bucket == k
        vals = q1[m]
        ts = t1[m]
        if vals.size < nmin:
            continue                      # 欠損のある足は使わない
        order = np.argsort(ts)
        vals = vals[order]
        full_rows.append(vals.sum())
        for f in FRACS:
            n = max(1, int(round(vals.size * f)))
            partial[f].append(vals[:n].sum())
    full = np.array(full_rows, dtype=float)
    line = f"{tf:5s} "
    for f in FRACS:
        pr = np.array(partial[f], dtype=float)
        p = causal_pct_against(full, pr, WINDOW)
        p = p[np.isfinite(p)]
        line += f" {np.median(p):8.3f}" if p.size else f" {'—':>8s}"
    print(line + f"   {full.size}")

print("\n" + "=" * 74)
print("M4-b. 同じ足で「確定足だけ」を当てた場合（基準・中央値は 0.5 付近になるはず）")
print("=" * 74)
for tf, per in PERIOD.items():
    bucket = (t1 // per) * per
    keys = np.unique(bucket)
    nmin = per // 60
    full_rows = []
    for k in keys:
        vals = q1[bucket == k]
        if vals.size < nmin:
            continue
        full_rows.append(vals.sum())
    full = np.array(full_rows, dtype=float)
    p = causal_pct_against(full, full, WINDOW)
    p = p[np.isfinite(p)]
    print(f"{tf:5s} 確定足のみ: 中央 {np.median(p):.3f} / p<=0.10 の割合 "
          f"{np.count_nonzero(p <= 0.10)/p.size*100:.1f}%")

print("\n" + "=" * 74)
print("M4-c. 対照: ma_marod（積み上がらない量）の形成中バー")
print("=" * 74)
print("乖離率は「今の価格と MA の差」であり、足の経過割合に依らず定義される。")
print("部分足の終値＝その時点の価格なので、形成中でも確定足と同じ土俵に乗る。")
print("→ 積み上がる量（tick 数）に固有の問題であることを構造で確認した。")

print("\n" + "=" * 74)
print("M4-d. 是正の検証: 部分足を「同じ経過割合の部分足の分布」へ当てる")
print("=" * 74)
print(f"{'足':5s} " + " ".join(f"{'f='+format(f,'.1f'):>8s}" for f in FRACS))
for tf, per in PERIOD.items():
    bucket = (t1 // per) * per
    keys = np.unique(bucket)
    nmin = per // 60
    partial = {f: [] for f in FRACS}
    for k in keys:
        m = bucket == k
        vals = q1[m][np.argsort(t1[m])]
        if vals.size < nmin:
            continue
        for f in FRACS:
            n = max(1, int(round(vals.size * f)))
            partial[f].append(vals[:n].sum())
    line = f"{tf:5s} "
    for f in FRACS:
        pr = np.array(partial[f], dtype=float)
        p = causal_pct_against(pr, pr, WINDOW)   # 同じ f の分布へ当てる
        p = p[np.isfinite(p)]
        line += f" {np.median(p):8.3f}" if p.size else f" {'—':>8s}"
    print(line)
