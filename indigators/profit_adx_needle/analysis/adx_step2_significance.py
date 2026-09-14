"""Step 2: profit_adx_needle の予測的有意性を実測する。

Step 1 で確定した前提:
  needle = 7 × (ADX(6) の直近120本因果 z)。repaint なし。クランプは非発動。
  ADX は |+DI - -DI| ベースで **方向性を持たない**（トレンド強度）。

したがって検定は 2 系統:
  H1 方向性: needle_t は符号付き将来リターンを予測するか（事前予想＝帰無）
  H2 大きさ: needle_t は将来の変動の大きさを予測するか（事前予想＝あり得る）
  H2' 増分 : H2 が成立しても、それは「直近ボラの z」で説明できる自明な情報でないか
             （ADX はレンジから作られるためボラと機械的に相関する）

帰無分布: 循環ブロック順列（signal 側のみブロック単位で巡回シフト・並べ替え）。
自己相関と重複窓を保存したまま signal↔target の対応だけを壊す。
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

from profit_adx_needle.src.core import (  # noqa: E402
    DEFAULT_PERIOD,
    DEFAULT_WINDOW,
    compute_adx_needle,
)

RNG = np.random.default_rng(20260726)
B_PERM = 2000


# ---------------------------------------------------------------- utilities
def load_tf(tf: str) -> pd.DataFrame:
    p = _ROOT / "data/marketdata/rollups" / f"jp225_m1_{tf}.csv"
    df = pd.read_csv(p, parse_dates=["date"])
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def rankdata(a: np.ndarray) -> np.ndarray:
    """平均順位（tie は平均）。scipy 非依存。"""
    n = a.size
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(n, dtype=np.float64)
    sa = a[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    xd = x - x.mean()
    yd = y - y.mean()
    d = math.sqrt(float(xd @ xd) * float(yd @ yd))
    return float(xd @ yd) / d if d > 0 else 0.0


def circular_block_perm(idx_n: int, block: int, rng) -> np.ndarray:
    """循環ブロック順列のインデックスを返す（長さ idx_n）。"""
    nb = int(math.ceil(idx_n / block))
    starts = rng.integers(0, idx_n, size=nb)
    out = np.concatenate([(np.arange(s, s + block) % idx_n) for s in starts])
    return out[:idx_n]


def perm_test_ic(sig: np.ndarray, tgt: np.ndarray, block: int, b: int = B_PERM):
    """Spearman IC と循環ブロック順列による両側 p 値。"""
    rs, rt = rankdata(sig), rankdata(tgt)
    obs = pearson(rs, rt)
    n = rs.size
    cnt = 0
    for _ in range(b):
        p = circular_block_perm(n, block, RNG)
        if abs(pearson(rs[p], rt)) >= abs(obs) - 1e-15:
            cnt += 1
    return obs, (cnt + 1) / (b + 1)


def newey_west_t(y: np.ndarray, x: np.ndarray, lag: int):
    """y = a + b*x の OLS 係数と Newey-West(HAC) t 値。"""
    n = y.size
    X = np.column_stack([np.ones(n), x])
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ (X.T @ y)
    resid = y - X @ beta
    u = X * resid[:, None]
    S = u.T @ u
    for L in range(1, lag + 1):
        w = 1.0 - L / (lag + 1.0)
        G = u[L:].T @ u[:-L]
        S += w * (G + G.T)
    cov = xtx_inv @ S @ xtx_inv
    se = math.sqrt(max(cov[1, 1], 0.0))
    return float(beta[1]), (float(beta[1]) / se if se > 0 else 0.0)


def norm_p_two(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def bh_fdr(pvals):
    """Benjamini-Hochberg 補正後 q 値。"""
    m = len(pvals)
    order = np.argsort(pvals)
    q = np.empty(m)
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        val = min(prev, pvals[i] * m / (rank + 1))
        q[i] = val
        prev = val
    return q


# ---------------------------------------------------------------- main
def analyse(tf: str, horizons, block_mult: int = 10):
    df = load_tf(tf)
    h_, l_, c_ = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    res = compute_adx_needle(h_, l_, c_, DEFAULT_PERIOD, window=DEFAULT_WINDOW)
    sig = res.needle
    logc = np.log(c_)

    # 直近ボラ z（対照変数）: 同じ 120 本窓での |1本リターン| の z
    r1 = np.diff(logc, prepend=logc[0])
    s = pd.Series(np.abs(r1))
    vol_z = ((s - s.rolling(DEFAULT_WINDOW).mean())
             / s.rolling(DEFAULT_WINDOW).std(ddof=0)).to_numpy()

    rows = []
    n = len(df)
    for hz in horizons:
        fwd = np.full(n, np.nan)
        fwd[:n - hz] = logc[hz:] - logc[:n - hz]
        m = np.isfinite(sig) & np.isfinite(fwd) & np.isfinite(vol_z)
        x, y, v = sig[m], fwd[m], vol_z[m]
        nn = x.size
        block = max(block_mult * hz, 20)

        # H1 方向性
        ic_d, p_d = perm_test_ic(x, y, block)
        # H2 大きさ
        ic_a, p_a = perm_test_ic(x, np.abs(y), block)
        # H2' 増分（直近ボラ z の順位効果を除去した残差 vs needle）
        ra, rv = rankdata(np.abs(y)), rankdata(v)
        rv_c = rv - rv.mean()
        resid_a = ra - rv_c * (float(rv_c @ (ra - ra.mean())) / float(rv_c @ rv_c))
        ic_i, p_i = perm_test_ic(x, resid_a, block)
        # 参考: 対照変数自身の大きさ予測力
        ic_v, _ = perm_test_ic(v, np.abs(y), block)

        b_d, t_d = newey_west_t(y, x, max(hz - 1, 1))
        rows.append(dict(tf=tf, h=hz, n=nn, ic_dir=ic_d, p_dir=p_d,
                         ic_abs=ic_a, p_abs=p_a, ic_inc=ic_i, p_inc=p_i,
                         ic_volz=ic_v, nw_t_dir=t_d, p_dir_nw=norm_p_two(t_d)))
    return pd.DataFrame(rows), sig, logc, vol_z, df


def main() -> None:
    print(f"[setting] period={DEFAULT_PERIOD} window={DEFAULT_WINDOW} "
          f"block-perm B={B_PERM} seed=20260726")
    all_rows = []
    for tf, hs in (("1D", [1, 3, 5, 10, 20]),
                   ("4h", [1, 3, 6, 12, 30]),
                   ("1h", [1, 4, 12, 24, 72])):
        t, *_ = analyse(tf, hs)
        all_rows.append(t)
        print(f"\n=== TF={tf} (n={t['n'].iloc[0]}) ===")
        print(t.to_string(index=False,
                          float_format=lambda v: f"{v:.4f}"))
    tab = pd.concat(all_rows, ignore_index=True)

    print("\n=== 多重比較補正（BH-FDR, 全 %d 検定 × 3 系統） ===" % len(tab))
    for col, pcol in (("ic_dir", "p_dir"), ("ic_abs", "p_abs"), ("ic_inc", "p_inc")):
        q = bh_fdr(tab[pcol].to_numpy())
        tab[f"q_{col}"] = q
        sig_n = int((q < 0.05).sum())
        print(f"  {col:8s}: q<0.05 の件数 = {sig_n}/{len(tab)}  "
              f"最小 q={q.min():.4f}  最大|IC|={tab[col].abs().max():.4f}")

    out = Path(__file__).resolve().parent / "out" / "adx_step2_table.csv"
    tab.to_csv(out, index=False)
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
