#!/usr/bin/env python3
"""押し目買い(EMA接触反発) vs ランダム買い 有意性検定（日足・全EMA長）

問い: 「押し目買いとランダム買いに有意性はない」は全EMA長で正しいか?
方法: 各EMA長で、上からEMAに接触した日(=押し目)に買い、+H日保有の前向きリターンを集計。
      同数 n のランダム日買い(+H保有)の平均リターン分布を M 回作り、
      p = P(ランダム平均 >= 押し目平均)。p>=0.05 なら「有意性なし(ランダム並み)」。

押し目接触(プロキシ・日足OHLC): その日の始値がEMA上(open>=ema) かつ 安値がEMA以下(low<=ema)
      = 上昇基調でEMA水準まで下押し→接触。エントリ/イグジットは終値基準(timing検定)。

データ: prototype_260626-01/data.json '1D'(JP225 日足)読み取り専用。
"""
import json
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

SEED = 20260629
M_RUNS = 5000
HORIZON = 20
EMA_LENS = [5, 22, 66, 132, 259]


def load_daily():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    o = np.array([x["open"] for x in c], dtype=float)
    h = np.array([x["high"] for x in c], dtype=float)
    low = np.array([x["low"] for x in c], dtype=float)
    cl = np.array([x["close"] for x in c], dtype=float)
    return o, h, low, cl


def ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def fwd_ret(close, idx, H):
    return (close[idx + H] - close[idx]) / close[idx] * 100.0


def run():
    o, h, low, cl = load_daily()
    n_bars = len(cl)
    rng = np.random.default_rng(SEED)
    H = HORIZON
    valid_hi = n_bars - 1 - H

    # 無条件(全日買い)ベースライン
    base = fwd_ret(cl, np.arange(0, valid_hi + 1), H)
    base_mean = float(base.mean())
    base_win = float((base > 0).mean()) * 100.0

    print("=" * 80)
    print("押し目買い(EMA接触反発) vs ランダム買い 有意性検定  JP225 日足")
    print(f"日足 {n_bars}本 / 保有 +{H}d / 終値基準timing / M={M_RUNS} / seed {SEED}")
    print(f"基準: 無条件(全日買い)+{H}d 平均 {base_mean:+.3f}% 勝率 {base_win:.1f}%")
    print("=" * 80)
    print(f"{'EMA':>5} {'押し目n':>7} {'平均%':>8} {'勝率':>7} "
          f"{'乱平均%':>9} {'p(乱>=押)':>10} {'α=押-無条件':>12} {'判定':>16}")
    print("-" * 80)

    summary = {"horizon": H, "m_runs": M_RUNS, "seed": SEED,
               "baseline_mean_pct": round(base_mean, 4), "lengths": {}}

    for L in EMA_LENS:
        e = ema(cl, L)
        # 上からEMAに接触(押し目): 始値がEMA上 かつ 安値がEMA以下
        touch = (o >= e) & (low <= e)
        idx = np.where(touch)[0]
        idx = idx[idx <= valid_hi]          # +H 先が引ける日のみ
        n = len(idx)
        if n < 10:
            print(f"{L:>5} {n:>7}  (サンプル不足)")
            continue
        strat = fwd_ret(cl, idx, H)
        s_mean = float(strat.mean())
        s_win = float((strat > 0).mean()) * 100.0

        # ランダム: 同数 n の一様ランダム日買い、平均リターン分布
        rand_means = np.empty(M_RUNS)
        for m in range(M_RUNS):
            r = (rng.random(n) * (valid_hi + 1)).astype(int)
            rand_means[m] = fwd_ret(cl, r, H).mean()
        p = float((rand_means >= s_mean).mean())     # 片側: 乱が押し目以上
        alpha = s_mean - base_mean

        if p < 0.05:
            verdict = "有意(優位あり)"
        elif p < 0.20:
            verdict = "弱い兆し"
        else:
            verdict = "有意性なし"
        print(f"{L:>5} {n:>7} {s_mean:7.3f}% {s_win:6.1f}% "
              f"{np.median(rand_means):8.3f}% {p:9.3f} {alpha:+11.3f}% {verdict:>16}")

        summary["lengths"][L] = {
            "n": n, "mean_pct": round(s_mean, 4), "win_pct": round(s_win, 2),
            "rand_mean_median": round(float(np.median(rand_means)), 4),
            "p_random_ge_strategy": round(p, 4),
            "alpha_vs_baseline_pct": round(alpha, 4),
            "verdict": verdict,
        }

    print("-" * 80)
    print("p>=0.05 → 押し目買いの平均リターンはランダム買いと統計的に区別できない=有意性なし。")
    json.dump(summary, open(os.path.join(OUT, "pullback_significance.json"), "w"),
              ensure_ascii=False, indent=2)
    print(f"\n出力: {OUT}/pullback_significance.json")
    return summary


if __name__ == "__main__":
    run()
