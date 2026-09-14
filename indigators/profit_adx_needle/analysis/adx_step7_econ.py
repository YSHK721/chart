"""Step 7: 交互作用の経済的意味（1D 限定・費用込み）。

Step 5/6 で残った所見: 1D で needle が高いほど日次リターンの平均回帰が強い。
統計的有意 ≠ 取引可能。費用控除後に残るかを実測する。

戦略: 逆張り（前日と逆方向に翌日建てる）を needle の水準で条件付ける。
費用: スプレッド 10 円/往復（ma_marod 検証の前例に一致）。
帰無: needle 系列のみ循環ブロック順列（同じ方向ルール・同じ建玉数でフィルタ位置のみ無意味化）。
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

RNG = np.random.default_rng(20260730)
SPREAD = 10.0
B = 3000


def main() -> None:
    df = load_tf("1D")
    h_, l_, c_ = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    sig = compute_adx_needle(h_, l_, c_, 6, window=120).needle
    logc = np.log(c_)
    r1 = np.diff(logc, prepend=logc[0])
    rn = np.full(len(c_), np.nan)
    rn[:-1] = logc[1:] - logc[:-1]
    cost = SPREAD / c_
    d = df["date"].to_numpy()

    m = np.isfinite(sig) & np.isfinite(rn) & np.isfinite(r1)
    sig, r1, rn, cost, d = sig[m], r1[m], rn[m], cost[m], d[m]
    base = -np.sign(r1)  # 逆張り

    print("=" * 86)
    print("Step 7: 1D 逆張りの needle 条件付け（費用: スプレッド10円/往復 ≈ "
          f"{1e4*cost.mean():.2f}bp）")
    print("=" * 86)
    print(f"  標本 n={sig.size}  {str(d[0])[:10]} .. {str(d[-1])[:10]}")

    def run(mask, label, pdo=True):
        pos = np.where(mask, base, 0.0)
        act = pos != 0
        turn = np.abs(np.diff(np.concatenate([[0.0], pos]))) / 2.0
        pnl = pos * rn - turn * cost
        n = int(act.sum())
        if n < 50:
            print(f"  {label:26s} n={n:5d}  （標本不足）")
            return
        tot = pnl[act]
        sd = tot.std(ddof=1)
        sharpe = tot.mean() / sd * np.sqrt(252) if sd > 0 else np.nan
        p = np.nan
        if pdo:
            obs = pnl.sum()
            cnt = 0
            for _ in range(B):
                perm = circular_block_perm(sig.size, 20, RNG)
                sp = sig[perm]
                mk = mask_fn(sp)
                p2 = np.where(mk, base, 0.0)
                t2 = np.abs(np.diff(np.concatenate([[0.0], p2]))) / 2.0
                if abs((p2 * rn - t2 * cost).sum()) >= abs(obs) - 1e-15:
                    cnt += 1
            p = (cnt + 1) / (B + 1)
        print(f"  {label:26s} n={n:5d}  平均={1e4*tot.mean():+7.2f}bp  "
              f"累積={100*pnl.sum():+7.2f}%  年率Sharpe={sharpe:+5.2f}  "
              f"勝率={100*(tot>0).mean():4.1f}%  順列p={p:.4f}" if np.isfinite(p) else
              f"  {label:26s} n={n:5d}  平均={1e4*tot.mean():+7.2f}bp  "
              f"累積={100*pnl.sum():+7.2f}%  年率Sharpe={sharpe:+5.2f}  "
              f"勝率={100*(tot>0).mean():4.1f}%")

    global mask_fn
    print("\n--- 全期間 ---")
    mask_fn = lambda s: np.ones(s.size, bool)  # noqa: E731
    run(np.ones(sig.size, bool), "逆張り（無条件・基準）", pdo=False)
    for q, lab in ((2 / 3, "上位1/3"), (0.8, "上位20%"), (0.9, "上位10%")):
        thr = np.quantile(sig, q)
        mask_fn = (lambda t: (lambda s: s >= t))(thr)  # noqa: E731
        run(sig >= thr, f"逆張り × needle {lab}")
    for q, lab in ((1 / 3, "下位1/3"), (0.2, "下位20%")):
        thr = np.quantile(sig, q)
        mask_fn = (lambda t: (lambda s: s <= t))(thr)  # noqa: E731
        run(sig <= thr, f"逆張り × needle {lab}")

    print("\n--- 期間分割（上位1/3 フィルタ・順列なし） ---")
    thr = np.quantile(sig, 2 / 3)
    for name, k in (("2012-2017", d < np.datetime64("2018-01-01")),
                    ("2018-2026", d >= np.datetime64("2018-01-01")),
                    ("直近2年", d >= np.datetime64("2024-07-01"))):
        pos = np.where((sig >= thr) & k, base, 0.0)
        turn = np.abs(np.diff(np.concatenate([[0.0], pos]))) / 2.0
        pnl = (pos * rn - turn * cost)[k]
        act = pos[k] != 0
        if act.sum() < 30:
            continue
        t = pnl[act]
        sd = t.std(ddof=1)
        print(f"  {name:10s} n={int(act.sum()):5d}  平均={1e4*t.mean():+7.2f}bp  "
              f"累積={100*pnl.sum():+7.2f}%  年率Sharpe="
              f"{t.mean()/sd*np.sqrt(252) if sd>0 else float('nan'):+5.2f}")


if __name__ == "__main__":
    main()
