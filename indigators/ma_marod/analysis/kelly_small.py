"""資金10万円・×1円CFD（1枚=指数×1円≈66,000円分）で組める現実的な配分の実測。"""
import runpy, numpy as np
from pathlib import Path as _P
g = runpy.run_path(str(_P(__file__).resolve().parent / "kelly_nanpin.py"))
trades, times = g["trades"], g["times"]

def report(weights, label):
    r = []
    for tr in trades:
        r.append(sum(weights.get(lv, 0.0) * (tr["px_exit"] / px - 1) for lv, _t, px in tr["entries"]))
    r = np.array(r)
    eq = np.cumprod(1 + r)
    curve = np.concatenate([[1.0], eq]); peak = np.maximum.accumulate(curve)
    mdd = float(((curve - peak) / peak).min()) * 100
    yrs = (times[-1] - times[0]) / (365.25 * 86400)
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100
    print(f"{label:<34} CAGR={cagr:>5.2f}%  最大DD={mdd:>6.1f}%  最悪トレード={r.min()*100:+.1f}%")

# 10万円・×1CFD: 1枚=約0.66倍。各段1枚（等枚数）と、1/1/2枚（深い段を厚く）
report({"q5": 0.66, "med": 0.66, "ext": 0.66}, "各段1枚（0.66倍ずつ・合計約2倍）")
report({"q5": 0.66, "med": 1.32, "ext": 0.66}, "1枚/2枚/1枚（第2段厚め・合計2.6倍）")
report({"q5": 0.66, "med": 0.66, "ext": 1.32}, "1枚/1枚/2枚（第3段厚め・合計2.6倍）")
report({"q5": 0.37, "med": 0.65, "ext": 0.43}, "参考: 理想の1/10ケリー配分")
