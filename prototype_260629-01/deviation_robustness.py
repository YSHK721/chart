#!/usr/bin/env python3
"""22EMA 下方乖離シグナルの期間ロバスト性検定  JP225 日足

問い: 「深い下方乖離で買うとランダム超」は全期間だけでなく各サブ期間でも再現するか?
      (EMA132は全期間p=0.09でも期間分割で崩れた=同じ関門を通す)
シグナル: 乖離率 = (close-EMA22)/EMA22*100 が閾値 TH 以下の日に買い、+20d。
判定: 期間を3分割。各期で「シグナル平均+20d」を、同じ期間内の同数ランダム買い分布と比較。
      α = シグナル平均 − その期の無条件平均。p = P(ランダム平均 ≥ シグナル平均)。
      α>0 かつ p<0.20 が3期中いくつ再現するかで本物度を判定。
データ: prototype_260626-01/data.json '1D' 読み取り専用。
"""
import json
import os
import datetime as dt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")

SEED = 20260629
M_RUNS = 5000
H = 20
SPAN = 22
THRESHOLDS = [-3.0, -5.0]


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return (np.array([x["time"] for x in c], np.int64),
            np.array([x["close"] for x in c], float))


def ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def fwd(close, idx, H):
    return (close[idx + H] - close[idx]) / close[idx] * 100.0


def p_random(close, lo, up, n, strat_mean, rng, M, H):
    rng_hi = up - 1
    cnt = 0
    for _ in range(M):
        r = lo + (rng.random(n) * (rng_hi - lo + 1)).astype(int)
        if fwd(close, r, H).mean() >= strat_mean:
            cnt += 1
    return cnt / M


def run():
    t, close = load()
    n = len(close)
    rng = np.random.default_rng(SEED)
    e = ema(close, SPAN)
    dev = (close - e) / e * 100.0
    warm = SPAN * 3
    hi = n - 1 - H

    def ymd(i):
        return dt.datetime.fromtimestamp(int(t[i]), dt.timezone.utc).date()

    bounds = [warm, warm + (hi - warm) // 3, warm + 2 * (hi - warm) // 3, hi + 1]

    print("=" * 88)
    print("22EMA 下方乖離シグナル 期間ロバスト性検定  JP225 日足  M=%d" % M_RUNS)
    print("=" * 88)

    summary = {"span": SPAN, "horizon": H, "thresholds": {}}
    for TH in THRESHOLDS:
        sig = (dev <= TH)
        idx_all = np.where(sig)[0]
        idx_all = idx_all[(idx_all >= warm) & (idx_all <= hi)]
        m_all = float(fwd(close, idx_all, H).mean())
        base_all = float(fwd(close, np.arange(warm, hi + 1), H).mean())
        p_all = p_random(close, warm, hi + 1, len(idx_all), m_all, rng, M_RUNS, H)
        print(f"\n■ 閾値 乖離 ≤ {TH:g}%   全期間: n={len(idx_all)}  "
              f"平均+20d {m_all:+.3f}%  無条件 {base_all:+.3f}%  α {m_all-base_all:+.3f}%  p {p_all:.3f}")
        print(f"  {'サブ期間':>23} {'n':>4} {'平均+20d':>9} {'無条件':>8} {'α':>8} {'p(乱≥)':>8} {'判定':>10}")

        subs = []
        for k in range(3):
            lo, up = bounds[k], bounds[k + 1]
            sel = idx_all[(idx_all >= lo) & (idx_all < up)]
            if len(sel) < 8:
                print(f"  {ymd(lo)}〜{ymd(up-1)} {len(sel):>4}  サンプル不足")
                subs.append({"from": str(ymd(lo)), "to": str(ymd(up-1)),
                             "n": int(len(sel)), "insufficient": True})
                continue
            bm = float(fwd(close, np.arange(lo, up), H).mean())
            sm = float(fwd(close, sel, H).mean())
            p = p_random(close, lo, up, len(sel), sm, rng, M_RUNS, H)
            a = sm - bm
            verdict = "再現(α+)" if (a > 0 and p < 0.20) else ("α+弱" if a > 0 else "α≤0")
            print(f"  {ymd(lo)}〜{ymd(up-1)} {len(sel):>4} {sm:8.3f}% {bm:7.3f}% "
                  f"{a:+7.3f}% {p:7.3f} {verdict:>10}")
            subs.append({"from": str(ymd(lo)), "to": str(ymd(up-1)), "n": int(len(sel)),
                         "mean": round(sm, 4), "base": round(bm, 4), "alpha": round(a, 4),
                         "p": round(p, 4), "verdict": verdict})

        ok = [s for s in subs if s.get("verdict") == "再現(α+)"]
        pos = [s for s in subs if s.get("alpha", -1) > 0]
        n_valid = len([s for s in subs if not s.get("insufficient")])
        print(f"  → 再現(α+,p<0.2): {len(ok)}/{n_valid}期   α>0: {len(pos)}/{n_valid}期")
        summary["thresholds"][TH] = {
            "n_all": int(len(idx_all)), "mean_all": round(m_all, 4),
            "alpha_all": round(m_all - base_all, 4), "p_all": round(p_all, 4),
            "subperiods": subs,
            "reproduce_count": len(ok), "alpha_pos_count": len(pos), "n_valid": n_valid,
        }

    print("\n" + "-" * 88)
    _verdict(summary)
    json.dump(summary, open(os.path.join(OUT, "deviation_robustness.json"), "w"),
              ensure_ascii=False, indent=2)
    print(f"\n出力: {OUT}/deviation_robustness.json")


def _verdict(summary):
    print("【総合判定】")
    for TH, r in summary["thresholds"].items():
        tag = ("全期で再現=本物寄り" if r["reproduce_count"] == r["n_valid"]
               else ("過半再現=有望だが要観察" if r["reproduce_count"] >= 2
                     else "一部のみ=不安定/βの疑い"))
        print(f"  乖離≤{float(TH):g}%: 再現 {r['reproduce_count']}/{r['n_valid']}期, "
              f"α>0 {r['alpha_pos_count']}/{r['n_valid']}期 → {tag}")


if __name__ == "__main__":
    run()
