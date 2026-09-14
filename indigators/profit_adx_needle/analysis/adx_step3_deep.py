"""Step 3: 追加検証（陽性対照 / パラメータ頑健性 / 分位 / 条件付き効果 / OOS）。

Step 2 で方向性・大きさとも BH 補正後に生き残らなかった。単一パラメータ・線形
（順位相関）だけの否定では不十分なため、以下を実測する。

A. 陽性対照   : 検定機構が「実在する効果」を検出できることの確認
B. パラメータ : period × window を走査（否定結果がパラメータ選択の産物でないか）
C. 分位       : 順位相関が拾えない非単調・裾の効果
D. 条件付き   : ADX の本来用途＝トレンド強度フィルタ。needle が
                リターン自己相関（順張り/逆張りの効き）を変調するか（交互作用）
E. OOS        : 期間分割で符号・大きさが持続するか
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

from profit_adx_needle.src.core import compute_adx_needle  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adx_step2_significance import (  # noqa: E402
    bh_fdr, circular_block_perm, load_tf, newey_west_t, norm_p_two,
    pearson, perm_test_ic, rankdata,
)

RNG = np.random.default_rng(20260727)


def prep(tf: str, period: int = 6, window: int = 120):
    df = load_tf(tf)
    h_, l_, c_ = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    sig = compute_adx_needle(h_, l_, c_, period, window=window).needle
    logc = np.log(c_)
    r1 = np.diff(logc, prepend=logc[0])
    s = pd.Series(np.abs(r1))
    vol_z = ((s - s.rolling(window).mean()) / s.rolling(window).std(ddof=0)).to_numpy()
    return df, sig, logc, r1, vol_z


def fwd(logc: np.ndarray, hz: int) -> np.ndarray:
    n = logc.size
    f = np.full(n, np.nan)
    f[:n - hz] = logc[hz:] - logc[:n - hz]
    return f


# ------------------------------------------------------------------ A: 陽性対照
def part_a() -> None:
    print("\n" + "=" * 78)
    print("A. 陽性対照 — 検定機構が実在効果を検出できるか（trailing vol z → |fwd ret|）")
    print("=" * 78)
    for tf in ("1D", "4h", "1h"):
        _, sig, logc, _, vol_z = prep(tf)
        f = fwd(logc, 1)
        m = np.isfinite(vol_z) & np.isfinite(f) & np.isfinite(sig)
        ic, p = perm_test_ic(vol_z[m], np.abs(f[m]), 20, b=2000)
        ic_n, p_n = perm_test_ic(sig[m], np.abs(f[m]), 20, b=2000)
        print(f"  TF={tf:3s} n={int(m.sum()):6d}  対照(vol z) IC={ic:+.4f} p={p:.4f}"
              f"   | needle IC={ic_n:+.4f} p={p_n:.4f}   比={abs(ic)/max(abs(ic_n),1e-9):5.1f}x")
    print("  ⇒ 対照が検出されれば機構は健全。needle の非有意は機構の不備ではない。")


# ------------------------------------------------------- B: パラメータ頑健性走査
def part_b() -> None:
    print("\n" + "=" * 78)
    print("B. パラメータ頑健性 — period × window 走査（否定がパラメータ依存でないか）")
    print("=" * 78)
    rows = []
    for tf, hs in (("1D", (1, 5, 20)), ("4h", (1, 6, 30)), ("1h", (1, 12, 72))):
        for period in (6, 14, 28):
            for window in (60, 120, 250):
                _, sig, logc, _, vol_z = prep(tf, period, window)
                for hz in hs:
                    f = fwd(logc, hz)
                    m = np.isfinite(sig) & np.isfinite(f) & np.isfinite(vol_z)
                    x, y, v = sig[m], f[m], vol_z[m]
                    blk = max(10 * hz, 20)
                    ic_d, p_d = perm_test_ic(x, y, blk, b=600)
                    ra, rv = rankdata(np.abs(y)), rankdata(v)
                    rvc = rv - rv.mean()
                    res = ra - rvc * (float(rvc @ (ra - ra.mean())) / float(rvc @ rvc))
                    ic_i, p_i = perm_test_ic(x, res, blk, b=600)
                    rows.append(dict(tf=tf, period=period, window=window, h=hz,
                                     n=int(m.sum()), ic_dir=ic_d, p_dir=p_d,
                                     ic_inc=ic_i, p_inc=p_i))
    t = pd.DataFrame(rows)
    t["q_dir"] = bh_fdr(t["p_dir"].to_numpy())
    t["q_inc"] = bh_fdr(t["p_inc"].to_numpy())
    print(f"  走査数 = {len(t)} 構成（TF3 × period3 × window3 × h3）")
    print(f"  方向性 : |IC|max={t['ic_dir'].abs().max():.4f}  生 p<0.05={int((t['p_dir']<0.05).sum())}"
          f"  BH q<0.05={int((t['q_dir']<0.05).sum())}")
    print(f"  増分   : |IC|max={t['ic_inc'].abs().max():.4f}  生 p<0.05={int((t['p_inc']<0.05).sum())}"
          f"  BH q<0.05={int((t['q_inc']<0.05).sum())}")
    print(f"  期待される偶然の生 p<0.05 件数 = {0.05*len(t):.1f}")
    print("\n  --- 生 p<0.05 の構成（多重比較前・参考） ---")
    sel = t[(t.p_dir < 0.05) | (t.p_inc < 0.05)]
    print(sel.to_string(index=False, float_format=lambda v: f"{v:.4f}") if len(sel)
          else "  （該当なし）")
    t.to_csv(Path(__file__).resolve().parent / "out" / "adx_step3_scan.csv", index=False)


# --------------------------------------------------------------- C: 分位分析
def part_c() -> None:
    print("\n" + "=" * 78)
    print("C. 分位分析 — 非単調・裾の効果（順位相関が拾えない形）")
    print("=" * 78)
    for tf, hz in (("1D", 5), ("4h", 6), ("1h", 12)):
        _, sig, logc, _, _ = prep(tf)
        f = fwd(logc, hz)
        m = np.isfinite(sig) & np.isfinite(f)
        x, y = sig[m], f[m]
        qs = np.quantile(x, np.linspace(0, 1, 11))
        qs[-1] += 1e-9
        b = np.digitize(x, qs[1:-1])
        print(f"\n  TF={tf} h={hz} n={x.size}  （needle 十分位ごとの将来リターン）")
        print("   dec   needle範囲          n   平均r(bp)   中央r(bp)  勝率   平均|r|(bp)")
        for k in range(10):
            s = b == k
            if not s.any():
                continue
            print(f"   {k+1:2d}  [{qs[k]:+7.2f},{qs[k+1]:+7.2f}] {int(s.sum()):5d} "
                  f"{1e4*y[s].mean():+9.2f} {1e4*np.median(y[s]):+10.2f} "
                  f"{100*(y[s]>0).mean():5.1f}% {1e4*np.abs(y[s]).mean():9.2f}")
        # 最上位 vs 最下位十分位の差の順列検定
        top, bot = y[b == 9], y[b == 0]
        obs = top.mean() - bot.mean()
        cnt = 0
        blk = max(10 * hz, 20)
        for _ in range(2000):
            p = circular_block_perm(x.size, blk, RNG)
            bb = b[p]
            d = y[bb == 9].mean() - y[bb == 0].mean()
            if abs(d) >= abs(obs) - 1e-18:
                cnt += 1
        print(f"   ⇒ 上位10% − 下位10% の平均リターン差 = {1e4*obs:+.2f}bp  "
              f"ブロック順列 p={(cnt+1)/2001:.4f}")


# ------------------------------------------------- D: 条件付き（本来用途の検証）
def part_d() -> None:
    print("\n" + "=" * 78)
    print("D. 条件付き効果 — ADX 本来用途（トレンド強度フィルタ）")
    print("   r[t+1] = a + b1*r[t] + b2*(r[t]×needle[t]) + b3*needle[t]")
    print("   b2 が有意なら『needle が順張り/逆張りの効きを変調する』＝実用的意味あり")
    print("=" * 78)
    rows = []
    for tf in ("1D", "4h", "1h", "5m"):
        try:
            _, sig, logc, r1, _ = prep(tf)
        except FileNotFoundError:
            continue
        n = len(sig)
        y = np.full(n, np.nan)
        y[:-1] = r1[1:]
        m = np.isfinite(sig) & np.isfinite(y) & np.isfinite(r1)
        rr, ss, yy = r1[m], sig[m], y[m]
        rr_s = rr / (np.std(rr) or 1.0)
        ss_s = (ss - ss.mean()) / (np.std(ss) or 1.0)
        X = np.column_stack([np.ones(rr.size), rr_s, rr_s * ss_s, ss_s])
        beta = np.linalg.pinv(X.T @ X) @ (X.T @ yy)
        resid = yy - X @ beta
        u = X * resid[:, None]
        S = u.T @ u
        for L in range(1, 6):
            w = 1.0 - L / 6.0
            G = u[L:].T @ u[:-L]
            S += w * (G + G.T)
        xtxi = np.linalg.pinv(X.T @ X)
        cov = xtxi @ S @ xtxi
        ts = [beta[k] / math.sqrt(max(cov[k, k], 0.0)) if cov[k, k] > 0 else 0.0
              for k in range(4)]
        rows.append(dict(tf=tf, n=int(m.sum()),
                         b_mom=beta[1], t_mom=ts[1], p_mom=norm_p_two(ts[1]),
                         b_inter=beta[2], t_inter=ts[2], p_inter=norm_p_two(ts[2]),
                         b_lvl=beta[3], t_lvl=ts[3], p_lvl=norm_p_two(ts[3])))
    t = pd.DataFrame(rows)
    print(t.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("\n  b_mom  = 素のリターン自己相関（逆張り性）")
    print("  b_inter= needle による自己相関の変調 ← 本指標の実用価値の核")
    print("  b_lvl  = needle 単体の方向性（H1 の再確認）")


# ----------------------------------------------------------------- E: OOS
def part_e() -> None:
    print("\n" + "=" * 78)
    print("E. 期間分割 — 効果の持続性（前半 / 後半 / 直近2年）")
    print("=" * 78)
    for tf, hz in (("1D", 1), ("4h", 1), ("1h", 1)):
        df, sig, logc, _, vol_z = prep(tf)
        f = fwd(logc, hz)
        m = np.isfinite(sig) & np.isfinite(f) & np.isfinite(vol_z)
        d = df["date"].to_numpy()
        idx = np.where(m)[0]
        half = idx[len(idx) // 2]
        rec = np.datetime64("2024-07-01")
        segs = (("前半", idx[idx <= half]), ("後半", idx[idx > half]),
                ("直近2年", idx[d[idx] >= rec]))
        print(f"\n  TF={tf} h={hz}")
        for name, ii in segs:
            if ii.size < 300:
                continue
            ic_d, p_d = perm_test_ic(sig[ii], f[ii], 20, b=1500)
            ic_a, p_a = perm_test_ic(sig[ii], np.abs(f[ii]), 20, b=1500)
            print(f"    {name:8s} n={ii.size:6d} [{str(d[ii[0]])[:10]}..{str(d[ii[-1]])[:10]}] "
                  f"方向 IC={ic_d:+.4f}(p={p_d:.3f})  大きさ IC={ic_a:+.4f}(p={p_a:.3f})")


if __name__ == "__main__":
    part_a()
    part_d()
    part_c()
    part_e()
    part_b()
