#!/usr/bin/env python3
"""ランダムエントリ n 回の最大DD / Ret/DD 分布（仮説検定）

問い/仮説: 「n 回ランダムに買うと、戦略より最大DDが広がるのではないか?」
方法: n 回のランダム買い(保有 H 日)を M 回試行し、期間内の最大DD と Ret/DD の分布を出す。
      戦略の実績値(別集計のコンタクト反発)を基準線に置き、ランダムがそれを上回る確率を測る。

集計規約(貼付け実績と同条件):
  1トレード = 1単位、損益は建値比 %、コスト 0。
  損益曲線 = 確定トレードをイグジット順に並べた累積 % 曲線。
  最大DD = その曲線のドローダウン(%ポイント)。Ret = 終端累積 %。Ret/DD = Ret / 最大DD。

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
HORIZON = 20  # 保有日(+20d)

# 戦略の実績基準線(別セッションのコンタクト反発集計 +20d より)
STRATEGY_REF = {
    "long_507": {"n": 507, "ret": 494.0, "max_dd": 104.0, "ret_dd": 4.73,
                 "label": "ロング反発(実績)"},
    "combined_906": {"n": 906, "ret": 178.0, "max_dd": 134.0, "ret_dd": 1.33,
                     "label": "合算(実績)"},
}


def load_daily():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return np.array([x["close"] for x in c], dtype=float)


def dd_and_ret(pnl_pct, exit_bar):
    """確定トレードをイグジット順に累積し、最大DD と Ret を返す（% 単位）。"""
    order = np.argsort(exit_bar, kind="stable")
    cum = np.cumsum(pnl_pct[order])
    run_max = np.maximum.accumulate(cum)
    max_dd = float((run_max - cum).max())
    ret = float(cum[-1])
    return ret, max_dd


def simulate(close, n, H, rng, M):
    """n 回ランダム買い(保有 H)を M 試行。各試行の (Ret, maxDD, Ret/DD) を返す。"""
    N = len(close)
    rets = np.empty(M)
    dds = np.empty(M)
    rdd = np.empty(M)
    hi = N - 1 - H
    for m in range(M):
        e0 = (rng.random(n) * (hi + 1)).astype(int)
        e1 = e0 + H
        pnl_pct = (close[e1] - close[e0]) / close[e0] * 100.0   # ロング・コスト0
        ret, max_dd = dd_and_ret(pnl_pct, e1)
        rets[m] = ret
        dds[m] = max_dd
        rdd[m] = ret / max_dd if max_dd > 0 else float("inf")
    return rets, dds, rdd


def pr(a, q):
    return float(np.percentile(a, q))


def run():
    close = load_daily()
    rng = np.random.default_rng(SEED)
    n_bars = len(close)

    print("=" * 78)
    print("ランダム買い n 回の 最大DD / Ret/DD 分布（日足・モンテカルロ）  JP225")
    print(f"日足 {n_bars}本 / 保有 +{HORIZON}d / 1トレード=1単位・%建値比・コスト0 / "
          f"M={M_RUNS} / seed {SEED}")
    print("仮説: ランダムの方が最大DDは広がる?")
    print("=" * 78)

    summary = {"horizon": HORIZON, "m_runs": M_RUNS, "seed": SEED, "cases": {}}

    for key, ref in STRATEGY_REF.items():
        n = ref["n"]
        rets, dds, rdd = simulate(close, n, HORIZON, rng, M_RUNS)

        # 仮説: ランダム最大DD > 戦略最大DD の確率
        p_dd_wider = float((dds > ref["max_dd"]).mean())
        # 戦略 Ret/DD はランダムより良いか: ランダム Ret/DD < 戦略 の割合
        rdd_fin = rdd[np.isfinite(rdd)]
        p_rdd_worse = float((rdd_fin < ref["ret_dd"]).mean())
        p_ret_higher = float((rets > ref["ret"]).mean())

        print(f"\n■ n={n}  ({ref['label']}: Ret +{ref['ret']:.0f}% / "
              f"最大DD {ref['max_dd']:.0f}% / Ret/DD {ref['ret_dd']:.2f})")
        print(f"  ランダム Ret    : 中央 {np.median(rets):+.0f}%  "
              f"[P5 {pr(rets,5):+.0f} / P95 {pr(rets,95):+.0f}]")
        print(f"  ランダム 最大DD : 中央 {np.median(dds):.0f}%  "
              f"[P5 {pr(dds,5):.0f} / P95 {pr(dds,95):.0f}]")
        print(f"  ランダム Ret/DD : 中央 {np.median(rdd_fin):.2f}  "
              f"[P5 {pr(rdd_fin,5):.2f} / P95 {pr(rdd_fin,95):.2f}]")
        print(f"  → ランダムDDが戦略({ref['max_dd']:.0f}%)より広い確率 : {p_dd_wider*100:.1f}%")
        print(f"  → ランダムRet/DDが戦略({ref['ret_dd']:.2f})より悪い確率: {p_rdd_worse*100:.1f}%")
        print(f"  → ランダムRetが戦略({ref['ret']:.0f}%)より高い確率   : {p_ret_higher*100:.1f}%")

        summary["cases"][key] = {
            "n": n, "ref": ref,
            "rand_ret_median": float(np.median(rets)),
            "rand_dd_median": float(np.median(dds)),
            "rand_dd_p5": pr(dds, 5), "rand_dd_p95": pr(dds, 95),
            "rand_retdd_median": float(np.median(rdd_fin)),
            "p_dd_wider_than_strategy": p_dd_wider,
            "p_retdd_worse_than_strategy": p_rdd_worse,
            "p_ret_higher_than_strategy": p_ret_higher,
        }

    json.dump(summary, open(os.path.join(OUT, "random_dd_test.json"), "w"),
              ensure_ascii=False, indent=2)
    _plot(close, rng, summary)
    print(f"\n出力: {OUT}/random_dd_test.json , random_dd_test.png")


def _plot(close, rng, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    keys = list(summary["cases"].keys())
    fig, ax = plt.subplots(2, len(keys), figsize=(12, 8))
    for j, key in enumerate(keys):
        c = summary["cases"][key]
        n = c["n"]
        rets, dds, rdd = simulate(close, n, summary["horizon"], rng, 3000)
        rdd = rdd[np.isfinite(rdd)]
        ref = c["ref"]
        ax[0][j].hist(dds, bins=60, color="#9bb", alpha=0.8)
        ax[0][j].axvline(ref["max_dd"], color="#c33", lw=2,
                         label=f"strategy DD {ref['max_dd']:.0f}%")
        ax[0][j].set_title(f"n={n}: Max DD (%)  [{ref['label']}]")
        ax[0][j].legend()
        ax[1][j].hist(rdd, bins=60, color="#bca", alpha=0.8)
        ax[1][j].axvline(ref["ret_dd"], color="#c33", lw=2,
                         label=f"strategy Ret/DD {ref['ret_dd']:.2f}")
        ax[1][j].set_title(f"n={n}: Ret/DD")
        ax[1][j].legend()
    fig.suptitle("Random LONG entry (gray) vs strategy actual (red line) — Max DD & Ret/DD",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "random_dd_test.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
