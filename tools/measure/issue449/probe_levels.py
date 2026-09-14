"""到達率・到達までの時間・反応を測るための素材を 52 日ぶん取得する（ISSUE-449 §9-1）。

各水準は**自分の時間足**で 1 回だけ取る（MTF は表示足に依らず値が同一なので投影しない）。
1m グリッドへは階段関数で写す。到達判定に high/low が要るので 1m ローソクも取る。
"""

# --- 実行時パス（唯一の設定） -------------------------------------------------
#   ISSUE449_DIR : 作業ディレクトリ。export.json を置き、生成物もここへ出す。
#   ISSUE449_API : ライブ計算サーバ。indigators/indicator_ui/serve.sh の内部ポート。
import os as _os
D = _os.environ.get("ISSUE449_DIR", _os.path.dirname(_os.path.abspath(__file__))) + "/"
BASE = _os.environ.get("ISSUE449_API", "http://127.0.0.1:8001")

import json, time, urllib.request

REF = 'jp225_tick'
TFS = ["1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M"]
PRICE = {"moving_averages", "btlm_trail", "cvfe"}
# 52 日を覆う本数（+ ウォームアップ）。上位足は履歴を厚めに取る。
LIM = {"1m": 50000, "5m": 15000, "15m": 6000, "1h": 2500,
       "4h": 2000, "1D": 2000, "1W": 2000, "1M": 2000}

raw = json.load(open(D + 'export.json'))
tpls = json.loads(raw["templates"])["templates"]
binds = json.loads(raw["bindings"])["bindings"]
byid = {t["templateId"]: t for t in tpls}
cat = json.loads(urllib.request.urlopen(BASE + "/catalog", timeout=30).read())["paramScopes"]


def post(body):
    req = urllib.request.Request(BASE + '/compute', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1200) as r:
        return json.loads(r.read())


SNAME = {"cvfe_u2": "cvfe 外側上 2σ", "cvfe_u1": "cvfe 内側上 1σ",
         "cvfe_l1": "cvfe 内側下 1σ", "cvfe_l2": "cvfe 外側下 2σ",
         "btlm_trail_q95": "btlm_trail q95", "btlm_trail_mean": "btlm_trail mean",
         "btlm_trail_q5": "btlm_trail q5",
         "btlm_trail_off_hi": "btlm_trail 外れ上", "btlm_trail_off_lo": "btlm_trail 外れ下"}

out, seen, errors = [], set(), []
t_start = time.perf_counter()
for tf in TFS:
    for inst in byid[binds[tf]]["instances"]:
        ind = inst["indicatorId"]
        if ind not in PRICE:
            continue
        allow = set(cat.get(ind, {}).get(inst.get("variant", "default")) or [])
        p = {k: v for k, v in inst["params"].items() if k in allow}
        own = p.pop("timeframe", None) or "chart"
        axis = own if own != "chart" else tf          # その水準を計算する足
        key = (ind, json.dumps(p, sort_keys=True), axis)
        if key in seen:
            continue                                   # 同じ水準は 1 回だけ取る
        seen.add(key)
        lim = LIM[axis]
        if ind == "cvfe":
            lim = max(lim, int(p.get("n_har", 0)) + 400)
        body = {"indicatorId": ind, "variant": inst.get("variant", "default"),
                "params": p, "datasetRef": REF, "generation": 0,
                "timeframe": axis, "limit": lim, "mode": "full"}
        t0 = time.perf_counter()
        try:
            res = post(body)
        except Exception as e:
            errors.append((axis, ind, str(e)[:80])); continue
        for s in res.get("series") or []:
            data = [d for d in (s.get("data") or []) if d.get("value") is not None]
            if not data:
                continue
            vals = [float(d["value"]) for d in data]
            mid = sorted(vals)[len(vals) // 2]
            if not (10000 < mid < 200000):             # 価格スケールでない系列は捨てる
                continue
            if ind == "moving_averages":
                label = f"MA {p['ma_type']}{p['length']} {p['source']}"
            else:
                label = SNAME.get(s["name"], s["name"])
            out.append({"label": label, "tf": axis, "ind": ind, "mtf": own != "chart",
                        "disp": tf,
                        "t": [int(d["time"]) for d in data],
                        "v": [round(float(d["value"]), 3) for d in data]})
        print(f"  {axis:4s} {ind:16s} lim={lim:6d} {(time.perf_counter()-t0)*1000:7.0f}ms")

with urllib.request.urlopen(
        f"{BASE}/candles?datasetRef={REF}&timeframe=1m&limit=50000", timeout=600) as r:
    candles = json.loads(r.read())["candles"]

json.dump({"series": out, "candles": candles, "errors": errors},
          open(D + 'long.json', 'w'))
print(f"\n水準 {len(out)} 本 / 1m ローソク {len(candles)} 本 / 失敗 {len(errors)} 件"
      f" / 所要 {time.perf_counter()-t_start:.0f}s")
for e in errors:
    print("   ", e)
