"""外れ値イベント（各時点で自身の因果閾値を超えた値）の分位を実測する。

サーバ（実データ・因果 per-bar 分位バンド）から ma_marod / q5 / q95 を取得し、
「正常バンド（0.05-0.95）超の値」をイベントとして収集。各バー t で過去イベントのみから
分位（中央値・0.9）を計算（因果）し、通常の全体分位 q99 と比較する。
"""
import json, urllib.request
import numpy as np

body = json.dumps({"indicatorId": "ma_marod", "variant": "default",
                   "params": {"source": "close", "ma_type": "ema", "length": 50,
                              "q_low": 0.05, "q_high": 0.95, "q_out": 0.99, "window_n": 500},
                   "datasetRef": "sample"}).encode()
req = urllib.request.Request("http://127.0.0.1:8281/compute", data=body,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as r:
    resp = json.load(r)
sm = {s["name"]: {d["time"]: d["value"] for d in s["data"]}
      for s in resp["series"] if s["kind"] == "line"}
times = sorted(set(sm["ma_marod"]) & set(sm["ma_marod_q5"]) & set(sm["ma_marod_q95"]))
v = np.array([sm["ma_marod"][t] for t in times])
ql_ = np.array([sm["ma_marod_q5"][t] for t in times])
qh_ = np.array([sm["ma_marod_q95"][t] for t in times])
off_hi = np.array([sm["ma_marod_off_hi"][t] for t in times]) if "ma_marod_off_hi" in sm else None

up_events, dn_events = [], []          # (index, value)
up_med, up_q90, dn_med, dn_q10 = [], [], [], []
for i in range(len(v)):
    ue = [x for _, x in up_events]
    de = [x for _, x in dn_events]
    up_med.append(np.median(ue) if len(ue) >= 5 else np.nan)
    up_q90.append(np.quantile(ue, 0.9) if len(ue) >= 5 else np.nan)
    dn_med.append(np.median(de) if len(de) >= 5 else np.nan)
    dn_q10.append(np.quantile(de, 0.1) if len(de) >= 5 else np.nan)
    if v[i] > qh_[i]:
        up_events.append((i, v[i]))
    elif v[i] < ql_[i]:
        dn_events.append((i, v[i]))

up_med, up_q90 = np.array(up_med), np.array(up_q90)
dn_med, dn_q10 = np.array(dn_med), np.array(dn_q10)
n = len(v)
print(f"バー数={n} 上側イベント={len(up_events)} ({len(up_events)/n:.1%}) 下側={len(dn_events)} ({len(dn_events)/n:.1%})")
print(f"直近値: 通常バンド q95={qh_[-1]:+.2f}% / q5={ql_[-1]:+.2f}%")
print(f"外れ値イベント分位（全履歴因果）: 上側 中央値={up_med[-1]:+.2f}% q90={up_q90[-1]:+.2f}% / 下側 中央値={dn_med[-1]:+.2f}% q10={dn_q10[-1]:+.2f}%")
m = np.isfinite(up_med)
print(f"参考: 上側イベント中央値の推移 min={np.nanmin(up_med):+.2f}% max={np.nanmax(up_med):+.2f}%")
print(f"参考: 下側イベント中央値の推移 min={np.nanmin(dn_med):+.2f}% max={np.nanmax(dn_med):+.2f}%")
# 到達率: 上側イベントのうち、イベント時点の「上側イベント中央値」以深まで行った割合
reach = [x >= up_med[i] for i, x in up_events if np.isfinite(up_med[i])]
reach_d = [x <= dn_med[i] for i, x in dn_events if np.isfinite(dn_med[i])]
print(f"イベントが当時の中央値水準へ到達した割合: 上側 {np.mean(reach):.1%} / 下側 {np.mean(reach_d):.1%}")
