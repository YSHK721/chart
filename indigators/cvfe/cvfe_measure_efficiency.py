"""ボラティリティ測定量の効率比較（σ=1 標準BM・DGP非依存）。

すべて「σ の推定量」に揃え、Var(ln sigma_hat) で比較する。
この分散がそのまま予測 R^2 の上限 R2 = Var(ln s)/(Var(ln s)+noise) を決める。
"""
from __future__ import annotations
import numpy as np

M = 1440          # 真の連続時間近似（1分足相当）
N = 200_000
CH = 20_000
rng = np.random.default_rng(11)

acc = {k: [] for k in ("r2", "park", "gk", "rs", "rv78", "rv288")}
for _ in range(N // CH):
    st = rng.standard_normal((CH, M)) / np.sqrt(M)
    p = np.cumsum(st, axis=1)
    o = np.zeros(CH); c = p[:, -1]
    h = np.maximum(p.max(1), 0.0); l = np.minimum(p.min(1), 0.0)

    acc["r2"].append(c ** 2)                                   # 終値収益^2
    acc["park"].append((h - l) ** 2 / (4 * np.log(2)))          # Parkinson 1980
    acc["gk"].append(0.5 * (h - l) ** 2 - (2 * np.log(2) - 1) * c ** 2)   # Garman-Klass 1980
    acc["rs"].append((h - c) * (h - o) + (l - c) * (l - o))     # Rogers-Satchell 1991
    for n, key in ((78, "rv78"), (288, "rv288")):               # 実現分散
        k = M // n
        r = p[:, k - 1::k] - np.concatenate([np.zeros((CH, 1)), p[:, k - 1::k][:, :-1]], axis=1)
        acc[key].append((r ** 2).sum(1))

names = {"r2": "終値収益^2", "park": "Parkinson", "gk": "Garman-Klass",
         "rs": "Rogers-Satchell", "rv78": "RV (5分足 78本)", "rv288": "RV (5分足 288本)"}
print(f"{'測定量':<20}{'E[σ̂²]':>9}{'Var(ln σ̂)':>12}{'効率(対 r²)':>13}")
print("-" * 55)
base = None
res = {}
for k, lab in names.items():
    v2 = np.concatenate(acc[k])
    v2 = v2[v2 > 0]
    lsig = 0.5 * np.log(v2)           # σ スケールの対数
    var = float(lsig.var())
    res[k] = var
    if base is None: base = var
    print(f"{lab:<20}{v2.mean():>9.4f}{var:>12.5f}{base/var:>13.1f}x")

print("\n予測 R² 上限 = Var(ln σ_t) / (Var(ln σ_t) + Var(ln σ̂))")
hdr = f"{'sd(ln σ_t)':<12}" + "".join(f"{names[k]:>18}" for k in ("r2", "park", "rv288"))
print(hdr)
for sd in (0.20, 0.25, 0.30, 0.40):
    row = f"{sd:<12.2f}"
    for k in ("r2", "park", "rv288"):
        row += f"{sd**2/(sd**2+res[k]):>17.1%} "
    print(row)
