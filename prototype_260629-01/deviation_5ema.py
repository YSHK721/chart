#!/usr/bin/env python3
"""5EMA からの下方乖離率の統計検証  JP225 日足

乖離率 = (close - EMA22) / EMA22 * 100。下方乖離 = 乖離率 < 0(終値がEMA下)。
報告:
  (1) 下方乖離の分布統計: EMA下にいる頻度、平均/中央/標準偏差、パーセンタイル、最大乖離。
  (2) 乖離の深さ別バケット × 先行+20dリターン: 深い下方乖離ほど反発が強いか(mean-reversion)を、
      同数ランダム買いに対する α と p値で判定。
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
SPAN = 5


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


def p_vs_random(close, hi, n, strat_mean, rng, M, H):
    cnt = 0
    for _ in range(M):
        r = (rng.random(n) * (hi + 1)).astype(int)
        if fwd(close, r, H).mean() >= strat_mean:
            cnt += 1
    return cnt / M


def run():
    t, close = load()
    n = len(close)
    rng = np.random.default_rng(SEED)
    e = ema(close, SPAN)
    dev = (close - e) / e * 100.0          # 乖離率%
    hi = n - 1 - H

    # 安定区間のみ(EMA立ち上がり除外)
    warm = SPAN * 3
    dev_w = dev[warm:]
    below = dev_w[dev_w < 0]
    above = dev_w[dev_w > 0]

    print("=" * 84)
    print(f"5EMA 下方乖離率の統計  JP225 日足  期間 "
          f"{dt.datetime.fromtimestamp(int(t[warm]),dt.timezone.utc).date()}〜"
          f"{dt.datetime.fromtimestamp(int(t[-1]),dt.timezone.utc).date()}")
    print("=" * 84)
    print("【1】乖離率の分布（warmup除外 %d日）" % len(dev_w))
    print(f"  EMA下にいる頻度 : {len(below)/len(dev_w)*100:.1f}%  "
          f"(上: {len(above)/len(dev_w)*100:.1f}%)")
    print(f"  全乖離率   平均 {dev_w.mean():+.3f}%  中央 {np.median(dev_w):+.3f}%  "
          f"標準偏差 {dev_w.std():.3f}%")
    print(f"  下方乖離   平均 {below.mean():.3f}%  中央 {np.median(below):.3f}%  "
          f"標準偏差 {below.std():.3f}%")
    qs = [50, 75, 90, 95, 99]
    deep = np.percentile(-below, qs)       # 下方乖離の深さ(絶対値)パーセンタイル
    print("  下方乖離の深さ(|乖離|)パーセンタイル:")
    for q, v in zip(qs, deep):
        print(f"    P{q:>2} = -{v:.2f}%", end="   ")
    print(f"\n    最大下方乖離 = {below.min():.2f}%")

    # 統計サマリ
    stats = {
        "span": SPAN, "days": int(len(dev_w)),
        "below_pct": round(len(below)/len(dev_w)*100, 2),
        "dev_mean": round(float(dev_w.mean()), 4),
        "dev_std": round(float(dev_w.std()), 4),
        "below_mean": round(float(below.mean()), 4),
        "below_std": round(float(below.std()), 4),
        "below_pctiles": {f"P{q}": round(-float(v), 3) for q, v in zip(qs, deep)},
        "below_min": round(float(below.min()), 3),
    }

    # 【2】乖離の深さ別 × 先行+20dリターン
    base = float(fwd(close, np.arange(warm, hi + 1), H).mean())
    print(f"\n【2】下方乖離の深さ別 × +{H}dリターン（基準=ランダム買い {base:+.3f}%）")
    print(f"  {'乖離バケット':>16} {'n':>5} {'平均乖離':>9} {'平均+20d':>10} {'勝率':>7} "
          f"{'α':>8} {'p(乱≥)':>8} {'判定':>10}")
    print("-" * 84)
    # バケット境界(乖離率%)
    edges = [(-1e9, -8), (-8, -5), (-5, -3), (-3, -2), (-2, -1), (-1, 0)]
    buckets = []
    for lo, up in edges:
        mask = np.zeros(n, bool)
        sel_idx = np.arange(warm, hi + 1)
        d = dev[sel_idx]
        in_b = (d > lo) & (d <= up)
        idx = sel_idx[in_b]
        if len(idx) < 10:
            continue
        mdev = float(dev[idx].mean())
        m20 = float(fwd(close, idx, H).mean())
        win = float((fwd(close, idx, H) > 0).mean()) * 100
        p = p_vs_random(close, hi, len(idx), m20, rng, M_RUNS, H)
        verdict = "意味あり" if p < 0.05 else ("弱い" if p < 0.20 else "ランダム並み")
        label = f"{up:g}〜{lo:g}%" if lo > -1e8 else f"≤{up:g}%"
        print(f"  {label:>16} {len(idx):>5} {mdev:8.2f}% {m20:9.3f}% {win:6.1f}% "
              f"{m20-base:+7.3f}% {p:7.3f} {verdict:>10}")
        buckets.append({"range": label, "n": int(len(idx)), "mean_dev": round(mdev, 3),
                        "mean_fwd20": round(m20, 4), "win": round(win, 2),
                        "alpha": round(m20 - base, 4), "p": round(p, 4), "verdict": verdict})

    print("-" * 84)
    print("深い下方乖離ほど α・勝率が上がれば「売られすぎ→反発」が定量的に成立。")
    print("注: p値は全期間。本物かは別途・期間分割のロバスト性検定が必要。")

    json.dump({"baseline_fwd20": round(base, 4), "stats": stats, "buckets": buckets},
              open(os.path.join(OUT, "deviation_5ema.json"), "w"),
              ensure_ascii=False, indent=2)
    _plot(dev_w, below, buckets, base)
    print(f"\n出力: {OUT}/deviation_5ema.json , deviation_5ema.png")


def _plot(dev_w, below, buckets, base):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    ax[0].hist(np.clip(dev_w, -12, 12), bins=80, color="#9ac", alpha=0.8)
    ax[0].axvline(0, color="k", lw=1)
    ax[0].axvline(dev_w.mean(), color="#c33", lw=1.5, label=f"mean {dev_w.mean():.2f}%")
    ax[0].set_title("5EMA deviation rate distribution (%)")
    ax[0].set_xlabel("(close-EMA)/EMA %"); ax[0].legend()

    labels = [b["range"] for b in buckets]
    alpha = [b["alpha"] for b in buckets]
    win = [b["win"] for b in buckets]
    x = np.arange(len(labels))
    ax2 = ax[1]; ax3 = ax2.twinx()
    ax2.bar(x, alpha, color="#6a6", alpha=0.7, label="alpha vs random (L)")
    ax3.plot(x, win, "o-", color="#c33", label="win rate % (R)")
    ax2.axhline(0, color="#999", lw=0.6)
    ax2.set_xticks(x); ax2.set_xticklabels(labels, rotation=20, fontsize=8)
    ax2.set_ylabel("alpha vs random (%/20d)"); ax3.set_ylabel("win rate %")
    ax2.set_title("Deeper downward deviation -> stronger bounce?")
    ax2.legend(loc="upper left"); ax3.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "deviation_5ema.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
