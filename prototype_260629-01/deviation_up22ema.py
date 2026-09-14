#!/usr/bin/env python3
"""22EMA からの上方乖離率の検証（下方乖離の鏡）  JP225 日足

乖離率 = (close - EMA22)/EMA22*100。上方乖離 = 乖離率 > 0(買われすぎ側)。
報告:
  (1) 上方乖離の分布統計。
  (2) 上方乖離の深さ別 × +20d: 「買い」リターンが深いほど下がるか(買われすぎ反落)を、
      同数ランダムに対する α(買い) と α(売り=−買い) と p値で判定。
  (3) 期間3分割ロバスト性（≥+3%, ≥+5%）。買われすぎ→反落(売りで勝てる)が時代不変か。
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
            np.array([x["close"] for x in c], float))


def ema(x, span):
    a = 2.0 / (span + 1.0)
    o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


def fwd(close, idx, H):
    return (close[idx + H] - close[idx]) / close[idx] * 100.0


def p_le(close, lo, up, n, sm, rng, M, H):
    """ランダム平均 ≥ sm の割合（買い基準で戦略が上位か）。"""
    hi = up - 1; cnt = 0
    for _ in range(M):
        r = lo + (rng.random(n) * (hi - lo + 1)).astype(int)
        if fwd(close, r, H).mean() >= sm:
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

    dev_w = dev[warm:]
    above = dev_w[dev_w > 0]
    print("=" * 88)
    print(f"22EMA 上方乖離率の検証（買われすぎ側）  JP225 日足  {ymd(warm)}〜{ymd(n-1)}")
    print("=" * 88)
    print("【1】上方乖離の分布")
    print(f"  EMA上にいる頻度 {len(above)/len(dev_w)*100:.1f}%  "
          f"上方乖離 平均 {above.mean():+.3f}%  中央 {np.median(above):+.3f}%  σ {above.std():.3f}%")
    qs = [50, 75, 90, 95, 99]
    up_q = np.percentile(above, qs)
    print("  上方乖離の高さパーセンタイル: " +
          "  ".join([f"P{q}=+{v:.2f}%" for q, v in zip(qs, up_q)]) +
          f"   最大 +{above.max():.2f}%")

    base = float(fwd(close, np.arange(warm, hi + 1), H).mean())
    print(f"\n【2】上方乖離の高さ別 × +{H}d（基準=ランダム買い {base:+.3f}%）")
    print(f"  {'乖離バケット':>14} {'n':>5} {'平均乖離':>9} {'買い+20d':>10} {'勝率':>7} "
          f"{'α買い':>8} {'α売り':>8} {'p(乱≥)':>8} {'判定(買い)':>12}")
    edges = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 1e9)]
    buckets = []
    for lo_e, up_e in edges:
        sel_idx = np.arange(warm, hi + 1)
        d = dev[sel_idx]
        idx = sel_idx[(d > lo_e) & (d <= up_e)]
        if len(idx) < 10:
            continue
        mdev = float(dev[idx].mean()); m20 = float(fwd(close, idx, H).mean())
        win = float((fwd(close, idx, H) > 0).mean()) * 100
        p = p_le(close, warm, hi + 1, len(idx), m20, rng, M_RUNS, H)
        a_buy = m20 - base
        a_sell = -m20 - (-base)   # 売りのα = (−買いリターン) − (−無条件)= base − m20
        v = "順張り◎" if (a_buy > 0 and p < 0.05) else (
            "買われすぎ(反落)" if a_buy < -0.0 and p > 0.80 else "ランダム並み")
        lab = f">{lo_e:g}%" if up_e > 1e8 else f"{lo_e:g}〜{up_e:g}%"
        print(f"  {lab:>14} {len(idx):>5} {mdev:8.2f}% {m20:9.3f}% {win:6.1f}% "
              f"{a_buy:+7.3f}% {a_sell:+7.3f}% {p:7.3f} {v:>12}")
        buckets.append({"range": lab, "n": int(len(idx)), "mean_dev": round(mdev, 3),
                        "buy_fwd20": round(m20, 4), "win": round(win, 2),
                        "alpha_buy": round(a_buy, 4), "alpha_sell": round(a_sell, 4),
                        "p_buy": round(p, 4)})

    print(f"\n【3】期間3分割ロバスト性（高い上方乖離の“買い”は順張りで効くか）")
    bounds = [warm, warm + (hi - warm)//3, warm + 2*(hi - warm)//3, hi + 1]
    robust = {}
    for TH in [3.0, 5.0]:
        idx_all = np.where(dev >= TH)[0]
        idx_all = idx_all[(idx_all >= warm) & (idx_all <= hi)]
        if len(idx_all) < 12:
            continue
        m_all = float(fwd(close, idx_all, H).mean())
        p_all = p_le(close, warm, hi+1, len(idx_all), m_all, rng, M_RUNS, H)
        print(f"  ■ 乖離 ≥ +{TH:g}%  全期間 n={len(idx_all)}  買いα {m_all-base:+.3f}%  p {p_all:.3f}")
        repro = 0; subs = []
        for k in range(3):
            lo_b, up_b = bounds[k], bounds[k+1]
            sel = idx_all[(idx_all >= lo_b) & (idx_all < up_b)]
            if len(sel) < 6:
                print(f"     {ymd(lo_b)}〜{ymd(up_b-1)} n={len(sel)} 不足"); continue
            bm = float(fwd(close, np.arange(lo_b, up_b), H).mean())
            sm = float(fwd(close, sel, H).mean())
            p = p_le(close, lo_b, up_b, len(sel), sm, rng, M_RUNS, H)
            a = sm - bm
            if a > 0 and p < 0.20: repro += 1
            tag = "買い再現" if (a>0 and p<0.20) else ("買い弱" if a>0 else "買い劣後(売り有利)")
            print(f"     {ymd(lo_b)}〜{ymd(up_b-1)} n={len(sel):>3} 買いα {a:+6.3f}% p {p:.3f}  {tag}")
            subs.append({"n": int(len(sel)), "alpha_buy": round(a,3), "p": round(p,3)})
        print(f"     → 買い再現 {repro}/3")
        robust[TH] = {"n": int(len(idx_all)), "alpha_buy_all": round(m_all-base,4),
                      "p_all": round(p_all,4), "buy_reproduce": repro, "subs": subs}

    print("-" * 88)
    print("α買い>0&p<0.05=順張り継続が有効 / α買い<0(=α売り>0)=買われすぎ反落で売り有利。")
    json.dump({"baseline": round(base,4),
               "above_freq_pct": round(len(above)/len(dev_w)*100,2),
               "buckets": buckets, "robust": robust},
              open(os.path.join(OUT, "deviation_up22ema.json"), "w"),
              ensure_ascii=False, indent=2)
    print(f"出力: {OUT}/deviation_up22ema.json")


if __name__ == "__main__":
    run()
