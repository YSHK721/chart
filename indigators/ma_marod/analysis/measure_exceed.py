import json, urllib.request
import numpy as np

def compute(params):
    body = json.dumps({"indicatorId": "ma_marod", "variant": "default",
                       "params": params, "datasetRef": "sample"}).encode()
    req = urllib.request.Request("http://127.0.0.1:8281/compute", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def series_map(resp):
    out = {}
    for s in resp["series"]:
        if s["kind"] != "line":
            continue
        out[s["name"]] = {d["time"]: d["value"] for d in s["data"]}
    return out

for q_out in (0.99, 0.995, 0.999):
    m = series_map(compute({"source": "close", "ma_type": "ema", "length": 50,
                            "q_low": 0.05, "q_high": 0.95, "q_out": q_out, "window_n": 500}))
    marod = m["ma_marod"]
    hi = m.get("ma_marod_off_hi", {})
    lo = m.get("ma_marod_off_lo", {})
    common = sorted(set(marod) & set(hi) & set(lo))
    v = np.array([marod[t] for t in common])
    h = np.array([hi[t] for t in common])
    l = np.array([lo[t] for t in common])
    above, below = int((v > h).sum()), int((v < l).sum())
    n = len(common)
    print(f"q_out={q_out}: n={n} above={above} ({above/n:.2%}) below={below} ({below/n:.2%}) "
          f"合計外={above+below} ({(above+below)/n:.2%})")

# 因果ローリング min/max（当該バー除外・window 500）での超過率＝バンド方式の理論限界
m = series_map(compute({"source": "close", "ma_type": "ema", "length": 50,
                        "q_low": 0.05, "q_high": 0.95, "q_out": None, "window_n": 500}))
times = sorted(m["ma_marod"])
v = np.array([m["ma_marod"][t] for t in times])
n = len(v); W = 500
above = below = valid = 0
for i in range(n):
    w = v[max(0, i - W):i]
    if w.size < 2:
        continue
    valid += 1
    if v[i] > w.max():
        above += 1
    elif v[i] < w.min():
        below += 1
print(f"rolling min/max: valid={valid} above={above} below={below} 合計外={above+below} ({(above+below)/valid:.2%})")
