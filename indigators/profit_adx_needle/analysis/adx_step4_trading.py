"""Step 4: 取引レベルの有意性 — needle を「レジームフィルタ」として使った場合の増分。

ADX は方向を持たないため、実務上の使い道は方向ルールへのフィルタ（トレンド強度が
高い時だけ順張り、低い時だけ逆張り 等）。本 Step はその増分価値を実測する。

帰無: needle 系列だけを循環ブロック順列でずらし、フィルタの当たり方をランダム化する。
      「同じ方向ルール・同じ取引回数・同じ自己相関構造」でフィルタ位置だけ無意味に
      した場合と比べ、実 needle が優位かを問う（＝フィルタ固有の情報の検定）。
費用: スプレッド 10 円/往復（ma_marod 検証の前例に一致）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "indigators"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adx_step2_significance import circular_block_perm, load_tf  # noqa: E402
from profit_adx_needle.src.core import compute_adx_needle  # noqa: E402

RNG = np.random.default_rng(20260728)
SPREAD_JPY = 10.0
B = 2000


def build(tf: str, period: int = 6, window: int = 120):
    df = load_tf(tf)
    h_, l_, c_ = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    sig = compute_adx_needle(h_, l_, c_, period, window=window).needle
    logc = np.log(c_)
    r_next = np.full(len(c_), np.nan)
    r_next[:-1] = logc[1:] - logc[:-1]
    cost = SPREAD_JPY / c_          # 往復コスト（対数近似・比率）
    return df, sig, logc, r_next, cost, c_


def rules(logc: np.ndarray) -> dict:
    n = logc.size
    r1 = np.diff(logc, prepend=logc[0])
    sma20 = pd.Series(logc).rolling(20).mean().to_numpy()
    sma50 = pd.Series(logc).rolling(50).mean().to_numpy()
    return {
        "mom1(順張り)": np.sign(r1),
        "rev1(逆張り)": -np.sign(r1),
        "sma20(順張り)": np.where(logc > sma20, 1.0, -1.0),
        "sma20x50": np.where(sma20 > sma50, 1.0, -1.0),
    }


def stats(pos: np.ndarray, r_next: np.ndarray, cost: np.ndarray):
    """ポジション系列の平均リターン・Sharpe（費用控除後・年率化なし）。"""
    m = np.isfinite(pos) & np.isfinite(r_next) & (pos != 0.0)
    if m.sum() < 50:
        return dict(n=int(m.sum()), mean_bp=np.nan, tstat=np.nan, hit=np.nan)
    p, r, cst = pos[m], r_next[m], cost[m]
    turn = np.abs(np.diff(np.concatenate([[0.0], p]))) / 2.0
    pnl = p * r - turn * cst
    sd = pnl.std(ddof=1)
    return dict(n=int(m.sum()), mean_bp=1e4 * pnl.mean(),
                tstat=(pnl.mean() / sd * np.sqrt(len(pnl))) if sd > 0 else np.nan,
                hit=100.0 * (pnl > 0).mean())


def main() -> None:
    print("=" * 82)
    print("Step 4: needle をレジームフィルタとして使った場合の増分（費用: スプレッド10円/往復）")
    print("=" * 82)
    for tf in ("1D", "4h", "1h"):
        df, sig, logc, r_next, cost, close = build(tf)
        print(f"\n### TF={tf}  bars={len(df)}  平均価格={close.mean():.0f} "
              f"往復コスト≈{1e4*cost.mean():.2f}bp")
        fin = np.isfinite(sig)
        hi = np.nanquantile(sig[fin], 2 / 3)
        lo = np.nanquantile(sig[fin], 1 / 3)
        print(f"    needle 三分位境界: lo={lo:+.2f} hi={hi:+.2f}")
        print(f"    {'ルール':16s} {'条件':22s} {'n':>7s} {'平均bp':>9s} {'t値':>8s} "
              f"{'勝率%':>7s} {'順列p':>7s}")
        for rname, base in rules(logc).items():
            base = np.where(np.isfinite(base), base, np.nan)
            st_all = stats(base, r_next, cost)
            print(f"    {rname:16s} {'フィルタ無し(基準)':22s} {st_all['n']:7d} "
                  f"{st_all['mean_bp']:9.3f} {st_all['tstat']:8.2f} {st_all['hit']:7.1f} "
                  f"{'-':>7s}")
            for cname, mask in (("needle高(上位1/3)", sig >= hi),
                                ("needle低(下位1/3)", sig <= lo)):
                pos = np.where(fin & mask, base, 0.0)
                st = stats(pos, r_next, cost)
                obs = st["mean_bp"]
                if not np.isfinite(obs):
                    continue
                # 帰無: needle 系列を循環ブロック順列 → フィルタ位置をランダム化
                cnt = 0
                nidx = sig.size
                for _ in range(B):
                    perm = circular_block_perm(nidx, 40, RNG)
                    sp = sig[perm]
                    mk = np.isfinite(sp) & ((sp >= hi) if "高" in cname else (sp <= lo))
                    s2 = stats(np.where(mk, base, 0.0), r_next, cost)
                    if np.isfinite(s2["mean_bp"]) and abs(s2["mean_bp"]) >= abs(obs) - 1e-12:
                        cnt += 1
                p = (cnt + 1) / (B + 1)
                print(f"    {'':16s} {cname:22s} {st['n']:7d} {obs:9.3f} "
                      f"{st['tstat']:8.2f} {st['hit']:7.1f} {p:7.4f}")


if __name__ == "__main__":
    main()
