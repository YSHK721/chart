#!/usr/bin/env python3
"""ランダム/無条件買いを n=EMA22押し目と同数で計測（速度の小標本分布）

目的: 押し目EMA22(n=565)の利益発生速度を、同数 n=565 のランダム買い分布と比較。
      ランダムを同じ n で引くことで「小標本のばらつき帯」を作り、EMA22の値が
      その範囲内(=ノイズ)か外(=実質差)かを判定する。
速度: 初動=R(5)/5、全速=R(20)/20  (%/日)。R(k)=平均 k日リターン。
データ: prototype_260626-01/data.json '1D' 読み取り専用。
"""
import json
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")

SEED = 20260629
M_RUNS = 5000
KMAX = 20


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return (np.array([x["open"] for x in c], float),
            np.array([x["low"] for x in c], float),
            np.array([x["close"] for x in c], float))


def ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def speed(close, idx, k):
    return ((close[idx + k] - close[idx]) / close[idx] * 100.0).mean() / k


def run():
    o, low, cl = load()
    n_bars = len(cl)
    rng = np.random.default_rng(SEED)
    hi = n_bars - 1 - KMAX

    # EMA22 押し目の実測
    e = ema(cl, 22)
    idx = np.where((o >= e) & (low <= e))[0]
    idx = idx[idx <= hi]
    N = len(idx)
    s22_init = speed(cl, idx, 5)
    s22_full = speed(cl, idx, 20)

    # n=N の一様ランダム買いを M 回 → 速度分布
    init = np.empty(M_RUNS)
    full = np.empty(M_RUNS)
    for m in range(M_RUNS):
        r = (rng.random(N) * (hi + 1)).astype(int)
        init[m] = speed(cl, r, 5)
        full[m] = speed(cl, r, 20)

    def loc(dist, val):
        p_le = float((dist <= val).mean())   # 乱がEMA22以下(=同程度に遅い)の割合
        return p_le

    pr = lambda a, q: float(np.percentile(a, q))
    print("=" * 76)
    print(f"ランダム買い(n={N}=EMA22押し目と同数) の利益速度分布  JP225 日足  M={M_RUNS}")
    print("=" * 76)
    print(f"{'指標':>12} {'EMA22押し目':>12} {'乱 中央':>9} {'乱 P5':>8} {'乱 P95':>8} "
          f"{'EMA22の位置':>12}")
    print("-" * 76)
    print(f"{'初動 %/日(5d)':>12} {s22_init:11.4f} {np.median(init):8.4f} "
          f"{pr(init,5):7.4f} {pr(init,95):7.4f}   下位{loc(init,s22_init)*100:5.1f}%")
    print(f"{'全速 %/日(20d)':>12} {s22_full:11.4f} {np.median(full):8.4f} "
          f"{pr(full,5):7.4f} {pr(full,95):7.4f}   下位{loc(full,s22_full)*100:5.1f}%")
    print("-" * 76)
    print("「下位x%」= 同数ランダムのうち EMA22 以下の速度になる割合。")
    print("  5〜95%の帯に収まれば、その遅さは小標本のばらつき範囲内＝有意な差ではない。")

    summary = {
        "n": int(N), "m_runs": M_RUNS, "seed": SEED,
        "ema22_init_speed": round(float(s22_init), 4),
        "ema22_full_speed": round(float(s22_full), 4),
        "rand_init": {"median": round(float(np.median(init)), 4),
                      "p5": round(pr(init, 5), 4), "p95": round(pr(init, 95), 4),
                      "pctile_of_ema22": round(loc(init, s22_init), 4)},
        "rand_full": {"median": round(float(np.median(full)), 4),
                      "p5": round(pr(full, 5), 4), "p95": round(pr(full, 95), 4),
                      "pctile_of_ema22": round(loc(full, s22_full), 4)},
    }
    json.dump(summary, open(os.path.join(OUT, "speed_randn.json"), "w"),
              ensure_ascii=False, indent=2)
    print(f"\n出力: {OUT}/speed_randn.json")


if __name__ == "__main__":
    run()
