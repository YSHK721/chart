"""下方バンドのケリー配分ナンピン検証（NI225 日足・実 HTTP 経路・因果ライン）。

トレード規約（エピソード単位）:
  - フラット時に marod が q5 を上→下クロス → 翌バー寄付でトランシェ1（ロング）。
  - 保有中に med_lo を下抜け → トランシェ2 追加。ext_lo を下抜け → トランシェ3 追加（各1回）。
  - 決済: marod が q5 を下→上クロス（正常域へ回復）で翌バー寄付・全玉。初回エントリーから
    20 バーでタイムアウト決済。データ末尾は強制決済。
サイズ: 連続ケリー f_i = μ_i/σ_i²（各ラインの独立実測 h=10 最終損益・インサンプル）。
  比較: フルケリー / ハーフケリー / 等ウェイト(1/3ずつ) / q5単発(100%)。
"""
import json, urllib.request
import numpy as np

BASE = "http://127.0.0.1:8281"

def fetch(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"} if body else {})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

cd = fetch(f"{BASE}/candles?datasetRef=jp225_tick&timeframe=1D")["candles"]
comp = fetch(f"{BASE}/compute", {"indicatorId": "ma_marod", "variant": "default",
                                  "params": {}, "datasetRef": "jp225_tick", "timeframe": "1D"})
sm = {s["name"]: {d["time"]: d["value"] for d in s["data"]}
      for s in comp["series"] if s["kind"] == "line"}
times = [c["time"] for c in cd]
o = np.array([c["open"] for c in cd]); cl = np.array([c["close"] for c in cd])
h_ = np.array([c["high"] for c in cd]); l_ = np.array([c["low"] for c in cd])
n = len(times)

def arr(name):
    m = sm.get(name, {})
    return np.array([m.get(t, np.nan) for t in times])

marod = arr("ma_marod")
q5 = arr("ma_marod_q5"); med = arr("ma_marod_evq_med_lo"); ext = arr("ma_marod_evq_ext_lo")

def cross_dn(line, t):
    return (np.isfinite(marod[t]) and np.isfinite(line[t]) and np.isfinite(marod[t-1])
            and np.isfinite(line[t-1]) and marod[t] <= line[t] and marod[t-1] > line[t-1])

def cross_up(line, t):
    return (np.isfinite(marod[t]) and np.isfinite(line[t]) and np.isfinite(marod[t-1])
            and np.isfinite(line[t-1]) and marod[t] > line[t] and marod[t-1] <= line[t-1])

TIMEOUT = 20
trades = []           # 各トレード: {"entries": [(level, t_entry, price)], "t_exit", "px_exit"}
state = None
for t in range(1, n - 1):
    if state is None:
        if cross_dn(q5, t):
            state = {"entries": [("q5", t + 1, o[t + 1])], "done": {"q5"}, "t0": t + 1}
    else:
        if cross_dn(med, t) and "med" not in state["done"]:
            state["entries"].append(("med", t + 1, o[t + 1])); state["done"].add("med")
        if cross_dn(ext, t) and "ext" not in state["done"]:
            state["entries"].append(("ext", t + 1, o[t + 1])); state["done"].add("ext")
        timeout = (t - state["t0"]) >= TIMEOUT
        if cross_up(q5, t) or timeout:
            state["t_exit"] = t + 1; state["px_exit"] = o[t + 1]
            trades.append(state); state = None
if state is not None:  # データ末尾強制決済
    state["t_exit"] = n - 1; state["px_exit"] = cl[-1]
    trades.append(state)

# ケリー比率（各ラインの独立実測 h=10 最終損益・前検証と同一規約で再計測）
def line_returns(line, hold=10):
    fins = []
    for t in range(1, n - hold - 1):
        if cross_dn(line, t):
            entry = o[t + 1]
            fins.append(cl[t + hold] / entry - 1)
    return np.array(fins)

kelly = {}
for name, line in (("q5", q5), ("med", med), ("ext", ext)):
    r = line_returns(line)
    mu, var = r.mean(), r.var(ddof=1)
    kelly[name] = max(0.0, mu / var) if var > 0 else 0.0
print("連続ケリー f=μ/σ²（h=10・インサンプル）:",
      {k: round(v, 2) for k, v in kelly.items()})

def simulate(weights, label):
    eq = 1.0; curve = [1.0]; rets = []
    for tr in trades:
        pnl = 0.0
        for level, _te, px in tr["entries"]:
            w = weights.get(level, 0.0)
            pnl += w * (tr["px_exit"] / px - 1)
        eq *= (1 + pnl); rets.append(pnl); curve.append(eq)
    curve = np.array(curve); rets = np.array(rets)
    peak = np.maximum.accumulate(curve); mdd = float(((curve - peak) / peak).min()) * 100
    yrs = (times[-1] - times[0]) / (365.25 * 86400)
    cagr = (eq ** (1 / yrs) - 1) * 100
    wr = float((rets > 0).mean()) * 100
    print(f"{label:<24} 総リターン={100*(eq-1):>8.1f}%  CAGR={cagr:>5.2f}%  最大DD={mdd:>6.1f}%  "
          f"勝率={wr:.1f}%  トレード中央値={np.median(rets)*100:+.2f}%  平均={rets.mean()*100:+.2f}%")
    return curve

n_multi = sum(1 for tr in trades if len(tr["entries"]) > 1)
n_ext = sum(1 for tr in trades if len(tr["entries"]) > 2)
hold_bars = [tr["t_exit"] - tr["t0"] for tr in trades]
print(f"\nトレード数={len(trades)}（ナンピン発動 {n_multi}・3段目まで {n_ext}）"
      f" 平均保有={np.mean(hold_bars):.1f}バー")
kf = kelly
simulate(kf, "フルケリー")
simulate({k: v / 2 for k, v in kf.items()}, "ハーフケリー")
simulate({"q5": 1/3, "med": 1/3, "ext": 1/3}, "等ウェイト(1/3)")
simulate({"q5": 1.0}, "q5単発(100%)")
# 参考: 総投下資本を1.0に正規化したケリー比率配分（レバレッジなし比較）
s = sum(kf.values())
simulate({k: v / s for k, v in kf.items()}, "ケリー比配分(合計100%)")

# 追補: フルケリー重みへの縮小係数 c の対数成長最大化（インサンプル）と各 c の DD
pnl_full = []
for tr in trades:
    pnl = sum(kf.get(level, 0.0) * (tr["px_exit"] / px - 1) for level, _t, px in tr["entries"])
    pnl_full.append(pnl)
pnl_full = np.array(pnl_full)
print("\n縮小係数 c（フルケリー×c）ごとの成績:")
for c in (0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0):
    r = c * pnl_full
    if (1 + r <= 0).any():
        print(f"  c={c:.1f}: 破産（1トレードで資本喪失）")
        continue
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
    mdd = float(((np.concatenate([[1.0], eq]) - peak) / peak).min()) * 100
    g = float(np.mean(np.log1p(r)))
    yrs = (times[-1] - times[0]) / (365.25 * 86400)
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100
    print(f"  c={c:.1f}: 対数成長/trade={g:+.4f}  総={100*(eq[-1]-1):>7.1f}%  CAGR={cagr:>5.2f}%  最大DD={mdd:>6.1f}%")
worst = trades[int(np.argmin(pnl_full))]
print(f"最悪トレード: エントリー {worst['entries']} → exit t={worst['t_exit']}  素損益={min(pnl_full)/sum(kf.values())*100:.2f}%×レバ")
