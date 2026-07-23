"""各ライン（q5/q95・evq_med_hi/lo・evq_ext_hi/lo）タッチ後の MFE/MAE/最終損益を実測する。

データ: 実 HTTP 経路（replay server /compute・/candles）・NI225 日足 jp225_tick 1D 全história。
タッチ定義: 内側からの初回クロス（上側: marod が線を下から上抜け／下側: 上から下抜け）。
トレード方向: 平均回帰想定（上側タッチ=ショート・下側タッチ=ロング）。
エントリー: タッチ確定の翌バー寄付（過去検定と同一規約）。保有 h バー。
MFE=保有中の最大含み益（%）・MAE=最大含み損（%・大きさ）・final=保有終了時の損益（%）。
"""
import json, urllib.request
import numpy as np

BASE = "http://127.0.0.1:8281"

def fetch(url, body=None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"} if body else {})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

cd = fetch(f"{BASE}/candles?datasetRef=jp225_tick&timeframe=1D")["candles"]
comp = fetch(f"{BASE}/compute", {
    "indicatorId": "ma_marod", "variant": "default", "params": {},
    "datasetRef": "jp225_tick", "timeframe": "1D"})
sm = {s["name"]: {d["time"]: d["value"] for d in s["data"]}
      for s in comp["series"] if s["kind"] == "line"}

times = [c["time"] for c in cd]
o = np.array([c["open"] for c in cd]); h_ = np.array([c["high"] for c in cd])
l_ = np.array([c["low"] for c in cd]); cl = np.array([c["close"] for c in cd])
n = len(times)

def arr(name):
    m = sm.get(name, {})
    return np.array([m.get(t, np.nan) for t in times])

marod = arr("ma_marod")
lines = {
    "q95(正常上端)": (arr("ma_marod_q95"), "short"),
    "evq_med_hi(典型上)": (arr("ma_marod_evq_med_hi"), "short"),
    "evq_ext_hi(極端上)": (arr("ma_marod_evq_ext_hi"), "short"),
    "q5(正常下端)": (arr("ma_marod_q5"), "long"),
    "evq_med_lo(典型下)": (arr("ma_marod_evq_med_lo"), "long"),
    "evq_ext_lo(極端下)": (arr("ma_marod_evq_ext_lo"), "long"),
}

def touches(line, side):
    ev = []
    for t in range(1, n):
        if not (np.isfinite(marod[t]) and np.isfinite(line[t])
                and np.isfinite(marod[t-1]) and np.isfinite(line[t-1])):
            continue
        if side == "short" and marod[t] >= line[t] and marod[t-1] < line[t-1]:
            ev.append(t)
        elif side == "long" and marod[t] <= line[t] and marod[t-1] > line[t-1]:
            ev.append(t)
    return ev

def stats(ev, side, hold):
    mfe, mae, fin = [], [], []
    for t in ev:
        e0, e1 = t + 1, t + hold
        if e1 >= n:
            continue
        entry = o[e0]
        hi = h_[e0:e1+1].max(); lo = l_[e0:e1+1].min(); c_end = cl[e1]
        if side == "long":
            mfe.append((hi/entry - 1) * 100); mae.append((1 - lo/entry) * 100)
            fin.append((c_end/entry - 1) * 100)
        else:
            mfe.append((1 - lo/entry) * 100); mae.append((hi/entry - 1) * 100)
            fin.append((entry/c_end - 1) * 100)
    return np.array(mfe), np.array(mae), np.array(fin)

print(f"バー数={n} 期間 {times[0]}..{times[-1]}")
for hold in (5, 10):
    print(f"\n== 保有 {hold} 日（タッチ翌バー寄付エントリー・%表示） ==")
    print(f"{'ライン':<18}{'方向':<6}{'n':>4} | {'MFE中央':>7} {'MFE平均':>7} | {'MAE中央':>7} {'MAE平均':>7} | {'最終中央':>7} {'最終平均':>7} {'勝率':>6}")
    for name, (line, side) in lines.items():
        ev = touches(line, side)
        mfe, mae, fin = stats(ev, side, hold)
        if len(fin) == 0:
            print(f"{name:<18}{side:<6}{len(ev):>4} | イベント不足")
            continue
        wr = float((fin > 0).mean()) * 100
        print(f"{name:<18}{side:<6}{len(fin):>4} | {np.median(mfe):>7.2f} {mfe.mean():>7.2f} | "
              f"{np.median(mae):>7.2f} {mae.mean():>7.2f} | {np.median(fin):>7.2f} {fin.mean():>7.2f} {wr:>5.1f}%")

# 追補: evq_med_lo ロングの最終損益の有意性（ブートストラップ・平均>0）と同期間ドリフト比較
rng = np.random.default_rng(42)
for hold in (5, 10):
    line, side = lines["evq_med_lo(典型下)"]
    ev = touches(line, side)
    _, _, fin = stats(ev, side, hold)
    boots = np.array([rng.choice(fin, fin.size, replace=True).mean() for _ in range(20000)])
    p = float((boots <= 0).mean())
    # ベースライン: 全バーから同数を無作為抽出したロング hold 日リターン（ドリフト）
    all_fin = []
    for t in range(1, n - hold - 1):
        all_fin.append((cl[t + hold] / o[t + 1] - 1) * 100)
    all_fin = np.array(all_fin)
    print(f"hold={hold}: med_lo long mean={fin.mean():.2f}% (n={fin.size}) "
          f"bootstrap p(mean<=0)={p:.4f} / 全バー無条件ロング平均={all_fin.mean():.2f}%")
