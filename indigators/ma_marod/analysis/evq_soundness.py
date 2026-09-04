"""外れ値イベント分位の統計的妥当性点検（実データ・因果バンドはサーバ算出）。

①バー単位 vs エピソード単位（連続超過を 1 エピソード＝極値 1 点に declustering）
②絶対値 vs 超過量（value - 当時の閾値）ベース
"""
import json, urllib.request
import numpy as np

body = json.dumps({"indicatorId": "ma_marod", "variant": "default",
                   "params": {}, "datasetRef": "sample"}).encode()
req = urllib.request.Request("http://127.0.0.1:8281/compute", data=body,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as r:
    resp = json.load(r)
sm = {s["name"]: {d["time"]: d["value"] for d in s["data"]}
      for s in resp["series"] if s["kind"] == "line"}
times = sorted(set(sm["ma_marod"]) & set(sm["ma_marod_q5"]) & set(sm["ma_marod_q95"]))
v = np.array([sm["ma_marod"][t] for t in times])
lo = np.array([sm["ma_marod_q5"][t] for t in times])
hi = np.array([sm["ma_marod_q95"][t] for t in times])

def analyze(side):
    if side == "up":
        mask = v > hi
        val, thr = v, hi
        ext = max
    else:
        mask = v < lo
        val, thr = v, lo
        ext = min
    bars = val[mask]
    excess = (val - thr)[mask]
    # runs declustering: 連続超過バーを 1 エピソードに（1 本でも内側に戻れば終了）
    episodes, ep_excess, cur, cur_e = [], [], [], []
    for i in range(len(v)):
        if mask[i]:
            cur.append(val[i]); cur_e.append(val[i] - thr[i])
        elif cur:
            episodes.append(ext(cur)); ep_excess.append(ext(cur_e)); cur, cur_e = [], []
    if cur:
        episodes.append(ext(cur)); ep_excess.append(ext(cur_e))
    episodes, ep_excess = np.array(episodes), np.array(ep_excess)
    name = "上側" if side == "up" else "下側"
    print(f"[{name}] イベントバー={len(bars)} エピソード={len(episodes)} "
          f"(平均持続 {len(bars)/len(episodes):.1f} 本)")
    print(f"  絶対値: バー中央値={np.median(bars):+.2f}% / エピソード極値中央値={np.median(episodes):+.2f}%")
    q = 0.99 if side == "up" else 0.01
    print(f"  絶対値: バーq99={np.quantile(bars, 0.99 if side=='up' else 0.01):+.2f}% / "
          f"エピソードq99={np.quantile(episodes, 0.99 if side=='up' else 0.01):+.2f}%")
    print(f"  超過量: バー中央値={np.median(excess):+.2f}pt / エピソード極値中央値={np.median(ep_excess):+.2f}pt")
    # 直近K=50イベントバーは実質何エピソード由来か
    idx = np.where(mask)[0][-50:]
    ep_count = 1 + int(np.sum(np.diff(idx) > 1))
    print(f"  直近K=50イベントバーの実質エピソード数={ep_count}（独立標本の過大評価率 {50/ep_count:.1f}倍）")

analyze("up")
analyze("dn")
