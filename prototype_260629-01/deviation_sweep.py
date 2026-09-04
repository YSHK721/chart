#!/usr/bin/env python3
"""下方乖離シグナルの EMA期間スイープ（5〜22）  JP225 日足

各EMA期間 span について、乖離率 ≤ TH の日に買い+20d のシグナルを、
全期間α/p と 期間3分割の再現数(α>0 かつ p<0.20 の期数) で横断比較する。
「逆張りに効く基準線の速さ」を一望する。
データ: prototype_260626-01/data.json '1D' 読み取り専用。
"""
import json
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")

SEED = 20260629
M_RUNS = 4000
H = 20
SPANS = list(range(5, 23))
THS = [-3.0, -5.0]


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return np.array([x["close"] for x in c], float)


def ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def fwd(close, idx, H):
    return (close[idx + H] - close[idx]) / close[idx] * 100.0


def p_random(close, lo, up, n, sm, rng, M, H):
    hi = up - 1
    cnt = 0
    for _ in range(M):
        r = lo + (rng.random(n) * (hi - lo + 1)).astype(int)
        if fwd(close, r, H).mean() >= sm:
            cnt += 1
    return cnt / M


def run():
    close = load()
    n = len(close)
    rng = np.random.default_rng(SEED)
    hi = n - 1 - H

    print("=" * 92)
    print("下方乖離シグナル EMA期間スイープ(5-22)  JP225 日足  +%dd / M=%d" % (H, M_RUNS))
    print("（再現 = 期間3分割で α>0 かつ p<0.20 の期数。3/3 がロバスト）")
    print("=" * 92)
    summary = {}
    for TH in THS:
        print(f"\n■ 閾値 乖離 ≤ {TH:g}%")
        print(f"  {'EMA':>4} {'n(全)':>6} {'全α':>8} {'全p':>7} "
              f"{'期1 α/p':>15} {'期2 α/p':>15} {'期3 α/p':>15} {'再現':>5}")
        summary[TH] = {}
        for span in SPANS:
            e = ema(close, span)
            dev = (close - e) / e * 100.0
            warm = span * 3
            bounds = [warm, warm + (hi - warm) // 3,
                      warm + 2 * (hi - warm) // 3, hi + 1]
            idx_all = np.where(dev <= TH)[0]
            idx_all = idx_all[(idx_all >= warm) & (idx_all <= hi)]
            if len(idx_all) < 12:
                print(f"  {span:>4} {len(idx_all):>6}   (サンプル不足)")
                summary[TH][span] = {"n": int(len(idx_all)), "insufficient": True}
                continue
            base_all = float(fwd(close, np.arange(warm, hi + 1), H).mean())
            m_all = float(fwd(close, idx_all, H).mean())
            p_all = p_random(close, warm, hi + 1, len(idx_all), m_all, rng, M_RUNS, H)

            cells = []
            repro = 0
            subs = []
            for k in range(3):
                lo, up = bounds[k], bounds[k + 1]
                sel = idx_all[(idx_all >= lo) & (idx_all < up)]
                if len(sel) < 6:
                    cells.append(f"{'n<6':>13}")
                    subs.append({"n": int(len(sel)), "insufficient": True})
                    continue
                bm = float(fwd(close, np.arange(lo, up), H).mean())
                sm = float(fwd(close, sel, H).mean())
                p = p_random(close, lo, up, len(sel), sm, rng, M_RUNS, H)
                a = sm - bm
                if a > 0 and p < 0.20:
                    repro += 1
                cells.append(f"{a:+5.2f}/{p:.2f}({len(sel)})")
                subs.append({"n": int(len(sel)), "alpha": round(a, 3), "p": round(p, 3)})
            mark = "★" if repro == 3 else ""
            print(f"  {span:>4} {len(idx_all):>6} {m_all-base_all:+7.3f}% {p_all:6.3f} "
                  f"{cells[0]:>15} {cells[1]:>15} {cells[2]:>15} {repro:>4}{mark}")
            summary[TH][span] = {"n": int(len(idx_all)),
                                 "alpha_all": round(m_all - base_all, 4),
                                 "p_all": round(p_all, 4),
                                 "reproduce": repro, "subs": subs}

    print("\n" + "-" * 92)
    print("★ = 3期すべてで再現（ロバスト）。短い線ほどノイズ・直近偏重で再現せず。")
    json.dump(summary, open(os.path.join(OUT, "deviation_sweep.json"), "w"),
              ensure_ascii=False, indent=2)
    _plot(summary)
    print(f"\n出力: {OUT}/deviation_sweep.json , deviation_sweep.png")


def _plot(summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    for j, TH in enumerate(THS):
        spans = [s for s in SPANS if not summary[TH][s].get("insufficient")]
        alpha = [summary[TH][s]["alpha_all"] for s in spans]
        repro = [summary[TH][s]["reproduce"] for s in spans]
        a2 = ax[j]; a3 = a2.twinx()
        a2.bar(spans, alpha, color="#6a6", alpha=0.6, label="full-period alpha (L)")
        a3.plot(spans, repro, "o-", color="#c33", label="reproduce /3 (R)")
        a2.axhline(0, color="#999", lw=0.6)
        a3.set_ylim(-0.2, 3.2)
        a2.set_xlabel("EMA span"); a2.set_ylabel("full alpha %/20d")
        a3.set_ylabel("reproduce count /3")
        a2.set_title(f"deviation <= {TH:g}%")
        a2.legend(loc="upper left"); a3.legend(loc="lower right")
    fig.suptitle("Downward-deviation mean-reversion: EMA span sweep (5-22)", fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "deviation_sweep.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
