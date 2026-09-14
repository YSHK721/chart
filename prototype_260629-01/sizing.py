#!/usr/bin/env python3
"""サイズ(レバレッジ)管理の検証（入口=ランダム買い・出口=固定20日に固定）  JP225 日足

問い: 入口・出口を固定し、建玉サイズ(レバ倍率 f)だけ変えると、
      資金ベース最大DD と 破産確率 はどう変わるか? 生存率はサイズで決まるか?
モデル: 初期資金 1.0。20日保有のロングを連続(複利)で T 回。
        各取引の資金変化 = equity *= (1 + f * r)、r = 実データの20日リターン(分布から iid)。
        破産 = 途中で 1+f*r<=0(追証で全損) もしくは 資金が初期の20%未満(実質再起不能)。
レバ f を振って CAGR / 資金最大DD / 破産確率 を比較。
注意: r は iid サンプル(自己相関は無視)。複利・単一銘柄・手数料は20d側に内包。
データ: prototype_260626-01/data.json '1D' 読み取り専用。
"""
import json
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")

SEED = 20260629
M_RUNS = 20000
T_TRADES = 176          # 14年 ÷ 20営業日 ≒ 176 連続トレード
COST_PTS = 7.0
LEVERAGES = [0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0]
RUIN_LEVEL = 0.20       # 初期資金の20%未満で実質破産


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return np.array([x["close"] for x in c], float)


def ret20_pool(close, H=20):
    n = len(close)
    idx = np.arange(0, n - H)
    cost = COST_PTS / close[idx]
    return (close[idx + H] - close[idx]) / close[idx] - cost   # 分数(コスト控除)


def simulate(pool, f, rng, M, T):
    """f 倍レバで T 回複利。各 run の (最終資金, 最大DD, 破産フラグ) を返す。"""
    final = np.empty(M); maxdd = np.empty(M); ruined = np.zeros(M, bool)
    for m in range(M):
        r = pool[(rng.random(T) * len(pool)).astype(int)]
        growth = 1.0 + f * r
        bust = growth <= 0
        eq = np.empty(T + 1); eq[0] = 1.0
        e = 1.0; busted = False; peak = 1.0; mdd = 0.0
        for j in range(T):
            if bust[j]:
                e = 0.0; busted = True
            else:
                e *= growth[j]
            peak = max(peak, e)
            dd = (peak - e) / peak if peak > 0 else 1.0
            mdd = max(mdd, dd)
            if e < RUIN_LEVEL:
                busted = True
            if busted:
                break
        final[m] = e
        maxdd[m] = mdd
        ruined[m] = busted
    return final, maxdd, ruined


def run():
    close = load()
    rng = np.random.default_rng(SEED)
    pool = ret20_pool(close)

    print("=" * 84)
    print("サイズ(レバレッジ)管理の検証  入口=ランダム買い/出口=固定20日  JP225 日足")
    print(f"連続{T_TRADES}取引(複利) × {M_RUNS}試行 / 20dリターン平均 {pool.mean()*100:+.2f}% / "
          f"破産=資金<{int(RUIN_LEVEL*100)}% / seed {SEED}")
    print("=" * 84)
    print(f"{'レバ':>6} {'最終資金中央(倍)':>15} {'年率(中央)':>11} {'資金最大DD中央':>14} "
          f"{'DD(P95)':>9} {'破産確率':>9}")
    print("-" * 84)

    years = T_TRADES * 20 / 252.0
    summary = {"t_trades": T_TRADES, "m_runs": M_RUNS, "ret20_mean_pct": round(float(pool.mean()*100), 3),
               "ruin_level": RUIN_LEVEL, "years": round(years, 1), "levels": {}}
    for f in LEVERAGES:
        final, maxdd, ruined = simulate(pool, f, rng, M_RUNS, T_TRADES)
        med_final = float(np.median(final))
        cagr = (med_final ** (1.0 / years) - 1.0) * 100 if med_final > 0 else -100.0
        med_dd = float(np.median(maxdd)) * 100
        p95_dd = float(np.percentile(maxdd, 95)) * 100
        ruin = float(ruined.mean()) * 100
        print(f"{f:5.1f}x {med_final:14.2f} {cagr:10.1f}% {med_dd:13.1f}% "
              f"{p95_dd:8.1f}% {ruin:8.1f}%")
        summary["levels"][f] = {
            "median_final_mult": round(med_final, 3),
            "cagr_pct": round(cagr, 2),
            "median_max_dd_pct": round(med_dd, 2),
            "p95_max_dd_pct": round(p95_dd, 2),
            "ruin_prob_pct": round(ruin, 2),
        }

    print("-" * 84)
    print("正のエッジ(β)があっても、レバを上げると資金DDと破産確率が急増。")
    print("中央の最終資金が頭打ち→低下する点が『過剰レバ』。生存はサイズで決まる。")
    json.dump(summary, open(os.path.join(OUT, "sizing.json"), "w"),
              ensure_ascii=False, indent=2)
    _plot(summary)
    print(f"\n出力: {OUT}/sizing.json , sizing.png")


def _plot(summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fs = sorted(summary["levels"].keys())
    cagr = [summary["levels"][f]["cagr_pct"] for f in fs]
    dd = [summary["levels"][f]["median_max_dd_pct"] for f in fs]
    ruin = [summary["levels"][f]["ruin_prob_pct"] for f in fs]
    fig, ax = plt.subplots(1, 3, figsize=(14, 4.5))
    ax[0].plot(fs, cagr, "o-", color="#36c"); ax[0].set_title("CAGR % (median)")
    ax[1].plot(fs, dd, "o-", color="#c63"); ax[1].set_title("Equity Max DD % (median)")
    ax[2].plot(fs, ruin, "o-", color="#c33"); ax[2].set_title("Ruin probability %")
    for a in ax:
        a.set_xlabel("leverage f (x)"); a.grid(alpha=0.3); a.axhline(0, color="#999", lw=0.6)
    fig.suptitle("Sizing/leverage sweep — same random LONG entry + 20d hold; only size differs",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "sizing.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
