"""Step 5: 唯一の候補所見（1D 交互作用）を敵対的に検証する。

Step 3-D で 1D のみ b_inter（needle × 前日リターン）が t=-2.65 / p=0.0081（NW 正規近似）。
「needle が高いほど日足の平均回帰が強まる」と読める。だが以下を通らなければ棄却する:

  5-1 ブロック順列による p（正規近似・HAC に頼らない）
  5-2 パラメータ頑健性（period × window の 9 構成）
  5-3 期間分割（前半 / 後半 / 直近2年）で符号と大きさが持続するか
  5-4 needle でなく素の ADX 水準・トレイリングボラ z で置換しても同じか
      （＝「needle 固有」か「ボラ代理でしかない」かの識別）
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "indigators"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adx_step2_significance import circular_block_perm, load_tf, norm_p_two  # noqa: E402
from profit_adx_needle.src.core import compute_adx, compute_adx_needle  # noqa: E402

RNG = np.random.default_rng(20260729)
B = 2000


def hac_t(y, X, lag=5):
    xtxi = np.linalg.pinv(X.T @ X)
    beta = xtxi @ (X.T @ y)
    resid = y - X @ beta
    u = X * resid[:, None]
    S = u.T @ u
    for L in range(1, lag + 1):
        w = 1.0 - L / (lag + 1.0)
        G = u[L:].T @ u[:-L]
        S += w * (G + G.T)
    cov = xtxi @ S @ xtxi
    return beta, np.array([beta[k] / math.sqrt(cov[k, k]) if cov[k, k] > 0 else 0.0
                           for k in range(X.shape[1])])


def setup(tf="1D", period=6, window=120, modulator="needle"):
    df = load_tf(tf)
    h_, l_, c_ = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    logc = np.log(c_)
    r1 = np.diff(logc, prepend=logc[0])
    if modulator == "needle":
        mod = compute_adx_needle(h_, l_, c_, period, window=window).needle
    elif modulator == "adx_raw":
        mod = compute_adx(h_, l_, c_, period)
    else:  # vol_z
        s = pd.Series(np.abs(r1))
        mod = ((s - s.rolling(window).mean()) / s.rolling(window).std(ddof=0)).to_numpy()
    y = np.full(len(c_), np.nan)
    y[:-1] = r1[1:]
    m = np.isfinite(mod) & np.isfinite(y) & np.isfinite(r1)
    return df, r1[m], mod[m], y[m], np.where(m)[0]


def design(r, mod):
    rs = r / (np.std(r) or 1.0)
    ms = (mod - mod.mean()) / (np.std(mod) or 1.0)
    return np.column_stack([np.ones(r.size), rs, rs * ms, ms]), rs, ms


def perm_p_inter(r, mod, y, block=20, b=B):
    X, rs, _ = design(r, mod)
    _, t = hac_t(y, X)
    obs = t[2]
    cnt = 0
    n = mod.size
    for _ in range(b):
        p = circular_block_perm(n, block, RNG)
        mp = mod[p]
        ms = (mp - mp.mean()) / (np.std(mp) or 1.0)
        Xp = np.column_stack([np.ones(n), rs, rs * ms, ms])
        _, tp = hac_t(y, Xp)
        if abs(tp[2]) >= abs(obs) - 1e-12:
            cnt += 1
    return obs, (cnt + 1) / (b + 1)


def main() -> None:
    print("=" * 84)
    print("Step 5: 候補所見『1D で needle が平均回帰を変調する』の敵対的検証")
    print("=" * 84)

    print("\n--- 5-1 ブロック順列 p（HAC 正規近似に依存しない） ---")
    _, r, mod, y, _ = setup()
    t_obs, p_perm = perm_p_inter(r, mod, y)
    print(f"  t_inter(観測) = {t_obs:+.4f}   正規近似 p = {norm_p_two(t_obs):.4f}   "
          f"ブロック順列 p = {p_perm:.4f}  (B={B}, block=20)")

    print("\n--- 5-2 パラメータ頑健性（period × window, 1D） ---")
    print(f"  {'period':>7s} {'window':>7s} {'n':>6s} {'b_inter':>10s} {'t_inter':>9s} "
          f"{'正規p':>8s} {'順列p':>8s}")
    rows = []
    for period in (6, 14, 28):
        for window in (60, 120, 250):
            _, r, mod, y, _ = setup(period=period, window=window)
            X, _, _ = design(r, mod)
            beta, t = hac_t(y, X)
            _, pp = perm_p_inter(r, mod, y, b=800)
            rows.append((period, window, r.size, beta[2], t[2], norm_p_two(t[2]), pp))
            print(f"  {period:7d} {window:7d} {r.size:6d} {beta[2]:+10.6f} {t[2]:+9.3f} "
                  f"{norm_p_two(t[2]):8.4f} {pp:8.4f}")
    sgn = [r[4] for r in rows]
    print(f"  ⇒ 符号一致: {sum(1 for s in sgn if s < 0)}/9 が負   "
          f"順列 p<0.05: {sum(1 for r in rows if r[6] < 0.05)}/9")

    print("\n--- 5-3 期間分割（既定パラメータ・1D） ---")
    df, r, mod, y, idx = setup()
    d = df["date"].to_numpy()[idx]
    half = r.size // 2
    segs = (("前半", slice(0, half)), ("後半", slice(half, None)),
            ("直近2年", np.where(d >= np.datetime64("2024-07-01"))[0]))
    for name, sl in segs:
        rr, mm, yy = r[sl], mod[sl], y[sl]
        if rr.size < 200:
            continue
        X, _, _ = design(rr, mm)
        beta, t = hac_t(yy, X)
        _, pp = perm_p_inter(rr, mm, yy, b=800)
        print(f"  {name:8s} n={rr.size:5d} [{str(d[sl][0])[:10]}..{str(d[sl][-1])[:10]}] "
              f"b_inter={beta[2]:+.6f} t={t[2]:+.3f} 順列p={pp:.4f}")

    print("\n--- 5-4 変調子の置換（needle 固有か / ボラ代理か） ---")
    print(f"  {'変調子':>12s} {'b_inter':>10s} {'t_inter':>9s} {'順列p':>8s}")
    for modname in ("needle", "adx_raw", "vol_z"):
        _, r, mod, y, _ = setup(modulator=modname)
        X, _, _ = design(r, mod)
        beta, t = hac_t(y, X)
        _, pp = perm_p_inter(r, mod, y, b=800)
        print(f"  {modname:>12s} {beta[2]:+10.6f} {t[2]:+9.3f} {pp:8.4f}")


if __name__ == "__main__":
    main()
