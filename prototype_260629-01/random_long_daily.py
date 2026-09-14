#!/usr/bin/env python3
"""ランダムロング検証（日足モンテカルロ）

目的: 「ランダムに買った場合」の損益を、実データの日足で実証分析する。
方針: 断定でなく実証。エントリのタイミングに優位性が無い(ランダム)とき、
      損益が何で決まるのか(=対象資産のドリフト)を計測で示す。

データ: prototype_260626-01/data.json の timeframe '1D'（JP225 日足, 2012-2026）を読み取り専用で使用。
出力 : prototype_260629-01/out/ に summary.json と ヒストグラム PNG。

ランダムエントリ = ランダムな日にロングを建て、H 営業日後の終値で決済。
往復コスト(スプレッド) を points で控除。決定論シード固定。
"""
import json
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

SEED = 20260629
N_TRIALS = 50_000          # 各ホライズンの試行数
COST_POINTS = 10.0         # 往復スプレッド(points)。JP225想定の控えめ値
HORIZONS = [1, 5, 20, 60, 120, 250]  # 保有営業日: 1日/1週/1月/3月/半年/1年


def load_daily():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    close = np.array([x["close"] for x in c], dtype=float)
    t = np.array([x["time"] for x in c], dtype=np.int64)
    return t, close


def pct(a, p):
    return float(np.percentile(a, p))


def run():
    t, close = load_daily()
    n = len(close)
    rng = np.random.default_rng(SEED)
    span_years = (t[-1] - t[0]) / (365.25 * 86400)

    print("=" * 72)
    print("ランダムロング検証（日足・モンテカルロ）  JP225")
    print(f"日足本数 {n}本  期間 {span_years:.1f}年  "
          f"始値 {close[0]:.0f} → 終値 {close[-1]:.0f}  "
          f"(全期間 {close[-1]/close[0]:.2f}倍)")
    print(f"試行 {N_TRIALS:,}/horizon  往復コスト {COST_POINTS}pt  seed {SEED}")
    print("=" * 72)

    summary = {
        "asset": "JP225",
        "bars": n,
        "span_years": round(span_years, 2),
        "first_close": close[0],
        "last_close": close[-1],
        "total_multiple": close[-1] / close[0],
        "n_trials": N_TRIALS,
        "cost_points": COST_POINTS,
        "seed": SEED,
        "horizons": {},
    }

    hdr = (f"{'保有':>6} {'平均±σ(pt)':>16} {'平均%':>8} {'中央%':>8} "
           f"{'勝率':>7} {'P5%':>8} {'P95%':>8} {'EV符号':>7}")
    print(hdr)
    print("-" * 72)

    for H in HORIZONS:
        # 終値[i] で建て, 終値[i+H] で決済できる i のみ対象
        idx = rng.integers(0, n - H, size=N_TRIALS)
        entry = close[idx]
        exit_ = close[idx + H]
        pnl_pt = (exit_ - entry) - COST_POINTS          # ロング損益(points)
        pnl_pct = pnl_pt / entry * 100.0                # 建値比%

        # 理論ドリフト: 全バーの H 日先フォワードリターン平均(コスト無)
        fwd = (close[H:] - close[:-H]) / close[:-H] * 100.0
        drift_pct = float(fwd.mean())

        win = float((pnl_pt > 0).mean()) * 100.0
        mean_pt = float(pnl_pt.mean())
        std_pt = float(pnl_pt.std())
        mean_pct = float(pnl_pct.mean())
        med_pct = float(np.median(pnl_pct))

        ev = "正(益)" if mean_pt > 0 else "負(損)"
        label = {1: "1日", 5: "1週", 20: "1月", 60: "3月",
                 120: "半年", 250: "1年"}.get(H, f"{H}日")
        print(f"{label:>6} {mean_pt:8.1f}±{std_pt:6.0f} {mean_pct:7.2f}% "
              f"{med_pct:7.2f}% {win:6.1f}% {pct(pnl_pct,5):7.2f}% "
              f"{pct(pnl_pct,95):7.2f}% {ev:>7}")

        summary["horizons"][H] = {
            "label": label,
            "hold_days": H,
            "mean_pnl_pt": round(mean_pt, 2),
            "std_pnl_pt": round(std_pt, 2),
            "mean_pnl_pct": round(mean_pct, 4),
            "median_pnl_pct": round(med_pct, 4),
            "win_rate_pct": round(win, 2),
            "p5_pct": round(pct(pnl_pct, 5), 4),
            "p25_pct": round(pct(pnl_pct, 25), 4),
            "p75_pct": round(pct(pnl_pct, 75), 4),
            "p95_pct": round(pct(pnl_pct, 95), 4),
            "min_pct": round(float(pnl_pct.min()), 4),
            "max_pct": round(float(pnl_pct.max()), 4),
            "drift_fwd_mean_pct": round(drift_pct, 4),  # コスト無の理論EV
        }

    print("-" * 72)
    print("注: 勝率/平均%は建値比。EV(平均)はエントリ優位性ではなく対象の")
    print("    ドリフト(上昇)で決まる → drift_fwd_mean_pct と平均%がほぼ一致。")

    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"),
              ensure_ascii=False, indent=2)

    _plot(t, close, rng, summary)
    print(f"\n出力: {OUT}/summary.json , random_long_daily.png")
    return summary


def _plot(t, close, rng, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import datetime as dt

    n = len(close)
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))

    # Left: PnL distribution (% of entry) for 3 horizons overlaid
    en = {1: "1d", 20: "1mo", 250: "1yr"}
    for H, col in [(1, "#888"), (20, "#2a7"), (250, "#c33")]:
        idx = rng.integers(0, n - H, size=30_000)
        pnl_pct = ((close[idx + H] - close[idx]) - summary["cost_points"]) / close[idx] * 100
        wr = summary['horizons'][H]['win_rate_pct']
        ax[0].hist(np.clip(pnl_pct, -40, 60), bins=80, alpha=0.5,
                   color=col, label=f"{en[H]} hold (win {wr:.0f}%)")
    ax[0].axvline(0, color="k", lw=0.8)
    ax[0].set_title("Random LONG entry: PnL distribution (% of entry)")
    ax[0].set_xlabel("PnL %")
    ax[0].set_ylabel("frequency")
    ax[0].legend()

    # Right: price path (drift visualization)
    dates = [dt.datetime.fromtimestamp(int(x), dt.timezone.utc) for x in t]
    ax[1].plot(dates, close, color="#36c", lw=0.8)
    ax[1].set_title(f"JP225 daily close ({close[-1]/close[0]:.1f}x over {summary['span_years']:.0f}y)")
    ax[1].set_xlabel("year")
    ax[1].set_ylabel("price")
    ax[1].grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "random_long_daily.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
