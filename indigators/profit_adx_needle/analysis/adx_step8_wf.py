"""Step 8: ウォークフォワード検証（閾値のインサンプル選択を排除）。

Step 7 の結果（needle 上位ほど逆張りが効く）は閾値を全期間で見てから選んでいる。
本 Step は「その時点までの情報だけ」で閾値を決め、以後の未見期間で執行する。

8-1 単純分割 WF : 前半で最良閾値を選び、後半で執行
8-2 逐次 WF    : 毎年、それまでのデータで閾値を選び翌年執行（アンカー拡張窓）
8-3 変調子比較 : needle / 素の ADX / ボラ z で同じ WF を実施
                （needle 固有の価値があるのか＝7×・z・σバンドの機構に意味があるか）
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
from profit_adx_needle.src.core import compute_adx, compute_adx_needle  # noqa: E402

RNG = np.random.default_rng(20260731)
SPREAD = 10.0
GRID = (0.50, 0.60, 2 / 3, 0.75, 0.80, 0.90)


def build(modulator="needle", period=6, window=120):
    df = load_tf("1D")
    h_, l_, c_ = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    logc = np.log(c_)
    r1 = np.diff(logc, prepend=logc[0])
    if modulator == "needle":
        mod = compute_adx_needle(h_, l_, c_, period, window=window).needle
    elif modulator == "adx_raw":
        mod = compute_adx(h_, l_, c_, period)
    else:
        s = pd.Series(np.abs(r1))
        mod = ((s - s.rolling(window).mean()) / s.rolling(window).std(ddof=0)).to_numpy()
    rn = np.full(len(c_), np.nan)
    rn[:-1] = logc[1:] - logc[:-1]
    m = np.isfinite(mod) & np.isfinite(rn) & np.isfinite(r1)
    return (df["date"].to_numpy()[m], -np.sign(r1[m]), mod[m], rn[m], SPREAD / c_[m])


def pnl_of(base, mod, rn, cost, sel):
    pos = np.where(sel, base, 0.0)
    turn = np.abs(np.diff(np.concatenate([[0.0], pos]))) / 2.0
    return pos * rn - turn * cost


def perf(p, act):
    t = p[act]
    if t.size < 20:
        return dict(n=int(t.size), mean_bp=np.nan, cum=np.nan, sharpe=np.nan)
    sd = t.std(ddof=1)
    return dict(n=int(t.size), mean_bp=1e4 * t.mean(), cum=100 * p.sum(),
                sharpe=t.mean() / sd * np.sqrt(252) if sd > 0 else np.nan)


def main() -> None:
    print("=" * 88)
    print("Step 8: ウォークフォワード（閾値をインサンプル選択しない）· 1D 逆張り × 変調子フィルタ")
    print(f"  閾値グリッド = 分位 {GRID}  費用 = スプレッド10円/往復")
    print("=" * 88)

    for modname in ("needle", "adx_raw", "vol_z"):
        d, base, mod, rn, cost = build(modname)
        print(f"\n### 変調子 = {modname}   n={mod.size}")

        # ---- 8-1 単純分割 WF ----
        half = mod.size // 2
        best_q, best_v = None, -1e18
        for q in GRID:
            thr = np.quantile(mod[:half], q)
            p = pnl_of(base[:half], mod[:half], rn[:half], cost[:half], mod[:half] >= thr)
            a = (mod[:half] >= thr)
            s = perf(p, a)
            if np.isfinite(s["sharpe"]) and s["sharpe"] > best_v:
                best_v, best_q = s["sharpe"], q
        thr = np.quantile(mod[:half], best_q)   # IS で決めた閾値をそのまま OOS に適用
        sel_oos = mod[half:] >= thr
        p_oos = pnl_of(base[half:], mod[half:], rn[half:], cost[half:], sel_oos)
        s_is = perf(pnl_of(base[:half], mod[:half], rn[:half], cost[:half],
                           mod[:half] >= thr), mod[:half] >= thr)
        s_oos = perf(p_oos, sel_oos)
        # 無条件逆張りの OOS（比較基準）
        s_base = perf(pnl_of(base[half:], mod[half:], rn[half:], cost[half:],
                             np.ones(mod.size - half, bool)), np.ones(mod.size - half, bool))
        print(f"  [8-1 単純分割] IS で選ばれた分位={best_q:.3f}(閾値{thr:+.3f}) "
              f"IS Sharpe={s_is['sharpe']:+.2f}")
        print(f"      OOS  [{str(d[half])[:10]}..{str(d[-1])[:10]}] n={s_oos['n']:4d} "
              f"平均={s_oos['mean_bp']:+7.2f}bp 累積={s_oos['cum']:+7.2f}% "
              f"Sharpe={s_oos['sharpe']:+5.2f}")
        print(f"      OOS 無条件逆張り(基準)      n={s_base['n']:4d} "
              f"平均={s_base['mean_bp']:+7.2f}bp 累積={s_base['cum']:+7.2f}% "
              f"Sharpe={s_base['sharpe']:+5.2f}")

        # ---- 8-2 逐次 WF（毎年再選択・アンカー拡張窓） ----
        yrs = pd.DatetimeIndex(d).year.to_numpy()
        uy = sorted(set(yrs.tolist()))
        acc, acc_act, picks = [], [], []
        for yy in uy:
            tr = yrs < yy
            te = yrs == yy
            if tr.sum() < 500 or te.sum() < 60:
                continue
            bq, bv = None, -1e18
            for q in GRID:
                th = np.quantile(mod[tr], q)
                a = mod[tr] >= th
                s = perf(pnl_of(base[tr], mod[tr], rn[tr], cost[tr], a), a)
                if np.isfinite(s["sharpe"]) and s["sharpe"] > bv:
                    bv, bq = s["sharpe"], q
            th = np.quantile(mod[tr], bq)
            a = mod[te] >= th
            acc.append(pnl_of(base[te], mod[te], rn[te], cost[te], a))
            acc_act.append(a)
            picks.append((yy, bq))
        if acc:
            P = np.concatenate(acc)
            A = np.concatenate(acc_act)
            s = perf(P, A)
            print(f"  [8-2 逐次 WF] 対象年={len(picks)} 選択分位={sorted(set(q for _, q in picks))}")
            print(f"      OOS 合成 n={s['n']:4d} 平均={s['mean_bp']:+7.2f}bp "
                  f"累積={s['cum']:+7.2f}% Sharpe={s['sharpe']:+5.2f}")
            # 帰無: 変調子のみ循環ブロック順列（同じ WF 手順を丸ごと再実行）
            obs = P.sum()
            cnt, B = 0, 500
            for _ in range(B):
                perm = circular_block_perm(mod.size, 20, RNG)
                mp = mod[perm]
                a2 = []
                for yy in uy:
                    tr = yrs < yy
                    te = yrs == yy
                    if tr.sum() < 500 or te.sum() < 60:
                        continue
                    bq, bv = None, -1e18
                    for q in GRID:
                        th = np.quantile(mp[tr], q)
                        aa = mp[tr] >= th
                        ss = perf(pnl_of(base[tr], mp[tr], rn[tr], cost[tr], aa), aa)
                        if np.isfinite(ss["sharpe"]) and ss["sharpe"] > bv:
                            bv, bq = ss["sharpe"], q
                    th = np.quantile(mp[tr], bq)
                    a2.append(pnl_of(base[te], mp[te], rn[te], cost[te], mp[te] >= th))
                if abs(np.concatenate(a2).sum()) >= abs(obs) - 1e-15:
                    cnt += 1
            print(f"      ブロック順列 p = {(cnt+1)/(B+1):.4f}  (B={B}・WF 手順ごと再実行)")


if __name__ == "__main__":
    main()
