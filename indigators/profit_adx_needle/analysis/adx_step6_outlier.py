"""Step 6: 候補所見の外れ値・単一エピソード依存の検証。

Step 5 で判明: 交互作用は 9/9 構成で符号一致・順列 p<0.05 が 7/9 だが、
**前半（2012-2019）では完全に消失し、後半（2019-2026）のみで有意**。
後半には COVID ショック（2020Q1）という極端な平均回帰エピソードが含まれる。

単一エピソードや裾の数点で駆動されていないかを以下で検証する:
  6-1 期間除外   : 2020Q1（COVID）を除いても残るか
  6-2 Winsorize  : リターンを 1%/99% で刈り込んでも残るか
  6-3 順位版     : 外れ値に鈍感な順位ベースでも残るか
  6-4 年次分解   : どの年が寄与しているか（leave-one-year-out）
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

from adx_step2_significance import load_tf, norm_p_two, rankdata  # noqa: E402
from adx_step5_candidate import design, hac_t, perm_p_inter  # noqa: E402
from profit_adx_needle.src.core import compute_adx_needle  # noqa: E402


def base(period=6, window=120):
    df = load_tf("1D")
    h_, l_, c_ = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    logc = np.log(c_)
    r1 = np.diff(logc, prepend=logc[0])
    mod = compute_adx_needle(h_, l_, c_, period, window=window).needle
    y = np.full(len(c_), np.nan)
    y[:-1] = r1[1:]
    m = np.isfinite(mod) & np.isfinite(y) & np.isfinite(r1)
    return df["date"].to_numpy()[m], r1[m], mod[m], y[m]


def report(tag, r, mod, y, b=800):
    if r.size < 200:
        print(f"  {tag:26s} n={r.size:5d}  （標本不足・スキップ）")
        return
    X, _, _ = design(r, mod)
    beta, t = hac_t(y, X)
    _, pp = perm_p_inter(r, mod, y, b=b)
    print(f"  {tag:26s} n={r.size:5d}  b_inter={beta[2]:+.6f}  t={t[2]:+7.3f}  "
          f"正規p={norm_p_two(t[2]):.4f}  順列p={pp:.4f}")
    return t[2]


def main() -> None:
    print("=" * 88)
    print("Step 6: 候補所見の外れ値・単一エピソード依存の検証（1D, period=6, window=120）")
    print("=" * 88)
    d, r, mod, y = base()

    print("\n--- 6-1 期間除外 ---")
    report("全期間（基準）", r, mod, y)
    for name, lo, hi in (("COVID除外(2020-02..05)", "2020-02-01", "2020-06-01"),
                         ("2020年まるごと除外", "2020-01-01", "2021-01-01")):
        k = ~((d >= np.datetime64(lo)) & (d < np.datetime64(hi)))
        report(name, r[k], mod[k], y[k])

    print("\n--- 6-2 Winsorize（リターンの裾を刈る） ---")
    for q in (0.01, 0.025, 0.05):
        ylo, yhi = np.quantile(y, q), np.quantile(y, 1 - q)
        rlo, rhi = np.quantile(r, q), np.quantile(r, 1 - q)
        report(f"winsor {int(q*100)}%/{int((1-q)*100)}%",
               np.clip(r, rlo, rhi), mod, np.clip(y, ylo, yhi))

    print("\n--- 6-3 順位版（外れ値に鈍感） ---")
    rr = (rankdata(r) - r.size / 2) / r.size
    yr = (rankdata(y) - y.size / 2) / y.size
    mr = (rankdata(mod) - mod.size / 2) / mod.size
    report("順位変換 r/y/mod", rr, mr, yr)

    print("\n--- 6-4 年次分解（leave-one-year-out で t_inter がどう動くか） ---")
    yrs = pd.DatetimeIndex(d).year.to_numpy()
    full_t = None
    X, _, _ = design(r, mod)
    _, t_all = hac_t(y, X)
    full_t = t_all[2]
    print(f"  全期間 t_inter = {full_t:+.3f}")
    print(f"  {'除外年':>8s} {'n':>6s} {'t_inter':>9s} {'変化':>8s}   {'当該年のみ t':>12s}")
    for yy in sorted(set(yrs.tolist())):
        k = yrs != yy
        if k.sum() < 300:
            continue
        Xk, _, _ = design(r[k], mod[k])
        _, tk = hac_t(y[k], Xk)
        s = yrs == yy
        t_only = np.nan
        if s.sum() >= 120:
            Xs, _, _ = design(r[s], mod[s])
            _, tsv = hac_t(y[s], Xs)
            t_only = tsv[2]
        print(f"  {yy:8d} {int(k.sum()):6d} {tk[2]:+9.3f} {tk[2]-full_t:+8.3f}   "
              f"{t_only:+12.3f}")


if __name__ == "__main__":
    main()
