"""med_lo までの 2 段ケリーナンピン（ext 段なし）検証。kelly_nanpin.py と同一規約。"""
import runpy, numpy as np
from pathlib import Path as _P
g = runpy.run_path(str(_P(__file__).resolve().parent / "kelly_nanpin.py"))
trades, kf, times = g["trades"], g["kelly"], g["times"]

kf2 = {"q5": kf["q5"], "med": kf["med"]}   # ext 段を外す（タッチしても追加しない）
print(f"\n===== med_lo まで 2 段ナンピン（ext なし・合計フルケリー={sum(kf2.values()):.1f}倍） =====")

def pnl_series(weights):
    out = []
    for tr in trades:
        out.append(sum(weights.get(lv, 0.0) * (tr["px_exit"] / px - 1) for lv, _t, px in tr["entries"]))
    return np.array(out)

def report(weights, label):
    r = pnl_series(weights)
    if (1 + r <= 0).any():
        print(f"{label:<22} 破産（1トレードで資本喪失）")
        return
    eq = np.cumprod(1 + r)
    curve = np.concatenate([[1.0], eq])
    peak = np.maximum.accumulate(curve)
    mdd = float(((curve - peak) / peak).min()) * 100
    yrs = (times[-1] - times[0]) / (365.25 * 86400)
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100
    wr = float((r > 0).mean()) * 100
    print(f"{label:<22} 総={100*(eq[-1]-1):>8.1f}%  CAGR={cagr:>5.2f}%  最大DD={mdd:>6.1f}%  "
          f"勝率={wr:.1f}%  平均={r.mean()*100:+.2f}%")

report(kf2, "フルケリー(2段)")
report({k: v/2 for k, v in kf2.items()}, "ハーフケリー(2段)")
pnl_full = pnl_series(kf2)
print("縮小係数 c ごと:")
for c in (0.1, 0.2, 0.3, 0.5, 0.7, 1.0):
    r = c * pnl_full
    if (1 + r <= 0).any():
        print(f"  c={c:.1f}: 破産")
        continue
    eq = np.cumprod(1 + r)
    curve = np.concatenate([[1.0], eq]); peak = np.maximum.accumulate(curve)
    mdd = float(((curve - peak) / peak).min()) * 100
    yrs = (times[-1] - times[0]) / (365.25 * 86400)
    cagr = (eq[-1] ** (1/yrs) - 1) * 100
    g_ = float(np.mean(np.log1p(r)))
    print(f"  c={c:.1f}: 対数成長/trade={g_:+.4f}  CAGR={cagr:>5.2f}%  最大DD={mdd:>6.1f}%")
# 最悪トレードの素損益（レバ前）
raw = []
for tr in trades:
    w = {"q5": 0.5, "med": 0.5}
    raw.append(sum(w.get(lv, 0.0) * (tr["px_exit"] / px - 1) for lv, _t, px in tr["entries"]))
i = int(np.argmin(pnl_full))
print(f"最悪トレード素損益（等加重換算）={raw[i]*100:.2f}%  フルケリー損益={pnl_full[i]*100:.1f}%")
