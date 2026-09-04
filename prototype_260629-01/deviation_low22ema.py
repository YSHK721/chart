#!/usr/bin/env python3
"""22日「安値」EMA からの下方乖離 検証  JP225 日足

基準線 = EMA(low, 22)（安値の22EMA＝下側サポートバンド）。
乖離率 = (close - emaLow)/emaLow * 100。close が emaLow を割り込む(<0)=深い売られすぎ。
報告: (1)分布統計  (2)深さ別×+20d(α/p)  (3)期間3分割ロバスト性。
比較対象: 終値EMA22(≤-5%が3期再現)。安値EMAでサポート割れを買うと有効か。
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


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return (np.array([x["time"] for x in c], np.int64),
            np.array([x["low"] for x in c], float),
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


def p_random(close, lo, up, n, sm, rng, M, H):
    hi = up - 1
    cnt = 0
    for _ in range(M):
        r = lo + (rng.random(n) * (hi - lo + 1)).astype(int)
        if fwd(close, r, H).mean() >= sm:
            cnt += 1
    return cnt / M


def run():
    t, low, close = load()
    n = len(close)
    rng = np.random.default_rng(SEED)
    elow = ema(low, SPAN)
    dev = (close - elow) / elow * 100.0
    warm = SPAN * 3
    hi = n - 1 - H

    def ymd(i):
        return dt.datetime.fromtimestamp(int(t[i]), dt.timezone.utc).date()

    dev_w = dev[warm:]
    below = dev_w[dev_w < 0]
    print("=" * 86)
    print(f"22日「安値」EMA 下方乖離 検証  JP225 日足  期間 {ymd(warm)}〜{ymd(n-1)}")
    print("=" * 86)
    print("【1】乖離率=(close-emaLow)/emaLow の分布（warmup除外 %d日）" % len(dev_w))
    print(f"  安値EMAを割り込む頻度(close<emaLow): {len(below)/len(dev_w)*100:.1f}%")
    print(f"  全乖離   平均 {dev_w.mean():+.3f}%  中央 {np.median(dev_w):+.3f}%  σ {dev_w.std():.3f}%")
    if len(below):
        qs = [50, 75, 90, 95, 99]
        deep = np.percentile(-below, qs)
        print(f"  割込み時の深さ |乖離| : " +
              "  ".join([f"P{q}=-{v:.2f}%" for q, v in zip(qs, deep)]) +
              f"   最大 {below.min():.2f}%")

    base = float(fwd(close, np.arange(warm, hi + 1), H).mean())
    print(f"\n【2】深さ別 × +{H}d（基準=ランダム {base:+.3f}%）")
    print(f"  {'乖離バケット':>14} {'n':>5} {'平均乖離':>9} {'平均+20d':>10} {'勝率':>7} "
          f"{'α':>8} {'p(乱≥)':>8} {'判定':>10}")
    edges = [(-1e9, -4), (-4, -2), (-2, 0), (0, 2), (2, 4)]
    buckets = []
    for lo_e, up_e in edges:
        sel_idx = np.arange(warm, hi + 1)
        d = dev[sel_idx]
        idx = sel_idx[(d > lo_e) & (d <= up_e)]
        if len(idx) < 10:
            continue
        mdev = float(dev[idx].mean()); m20 = float(fwd(close, idx, H).mean())
        win = float((fwd(close, idx, H) > 0).mean()) * 100
        p = p_random(close, warm, hi + 1, len(idx), m20, rng, M_RUNS, H)
        v = "意味あり" if p < 0.05 else ("弱い" if p < 0.20 else "ランダム並み")
        lab = f"{up_e:g}〜{lo_e:g}%" if lo_e > -1e8 else f"≤{up_e:g}%"
        print(f"  {lab:>14} {len(idx):>5} {mdev:8.2f}% {m20:9.3f}% {win:6.1f}% "
              f"{m20-base:+7.3f}% {p:7.3f} {v:>10}")
        buckets.append({"range": lab, "n": int(len(idx)), "mean_dev": round(mdev, 3),
                        "fwd20": round(m20, 4), "win": round(win, 2),
                        "alpha": round(m20-base, 4), "p": round(p, 4)})

    print(f"\n【3】期間3分割ロバスト性（割込み深さ別の閾値）")
    bounds = [warm, warm + (hi - warm)//3, warm + 2*(hi - warm)//3, hi + 1]
    robust = {}
    for TH in [0.0, -2.0]:
        idx_all = np.where(dev <= TH)[0]
        idx_all = idx_all[(idx_all >= warm) & (idx_all <= hi)]
        if len(idx_all) < 12:
            continue
        m_all = float(fwd(close, idx_all, H).mean())
        p_all = p_random(close, warm, hi+1, len(idx_all), m_all, rng, M_RUNS, H)
        print(f"  ■ 乖離 ≤ {TH:g}%  全期間 n={len(idx_all)}  α {m_all-base:+.3f}%  p {p_all:.3f}")
        repro = 0; subs = []
        for k in range(3):
            lo_b, up_b = bounds[k], bounds[k+1]
            sel = idx_all[(idx_all >= lo_b) & (idx_all < up_b)]
            if len(sel) < 6:
                print(f"     {ymd(lo_b)}〜{ymd(up_b-1)}  n={len(sel)} 不足"); continue
            bm = float(fwd(close, np.arange(lo_b, up_b), H).mean())
            sm = float(fwd(close, sel, H).mean())
            p = p_random(close, lo_b, up_b, len(sel), sm, rng, M_RUNS, H)
            a = sm - bm
            if a > 0 and p < 0.20: repro += 1
            print(f"     {ymd(lo_b)}〜{ymd(up_b-1)} n={len(sel):>3} α {a:+6.3f}% p {p:.3f} "
                  f"{'再現' if (a>0 and p<0.20) else ('α+弱' if a>0 else 'α≤0')}")
            subs.append({"n": int(len(sel)), "alpha": round(a,3), "p": round(p,3)})
        print(f"     → 再現 {repro}/3{'  ★' if repro==3 else ''}")
        robust[TH] = {"n": int(len(idx_all)), "alpha_all": round(m_all-base,4),
                      "p_all": round(p_all,4), "reproduce": repro, "subs": subs}

    print("-" * 86)
    json.dump({"baseline": round(base,4), "below_freq_pct": round(len(below)/len(dev_w)*100,2),
               "buckets": buckets, "robust": robust},
              open(os.path.join(OUT, "deviation_low22ema.json"), "w"),
              ensure_ascii=False, indent=2)
    print(f"出力: {OUT}/deviation_low22ema.json")


if __name__ == "__main__":
    run()
