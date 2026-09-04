#!/usr/bin/env python3
"""MA_Slope 戦略 vs ランダムエントリ（日足・モンテカルロ有意性検定）

問い: 「移動平均線戦略の取引回数(ロング/ショート)をランダムにエントリーした分布と
       比べれば、MA戦略の有意性を判定できるか?」

方法(モンテカルロ並べ替え検定):
  1. MA_Slope(EMA20傾き・ドテン・常時ポジション)を日足で走らせ、確定トレード列を得る。
     → 取引回数 N、ロング数 N_long、ショート数 N_short、各保有期間、各方向が確定。
  2. 同じ統計量を保ったランダム対照を 2 系統作り、それぞれ M 回試行して分布を得る:
     RND-1 (タイミング無効化): 各トレードの[保有期間・方向]は戦略と同一。
            エントリ時点だけ一様ランダム。→「いつ入るか」に優位性があるか。
     RND-2 (方向無効化)     : 各トレードの[エントリ/イグジット時点]は戦略と同一。
            売買方向だけシャッフル(N_long/N_short は不変)。→「方向選択」に優位性があるか。
  3. 戦略の総損益・最大DD が、各ランダム分布の何パーセンタイルかで p 値を出す。

注意(この検定で言えること/言えないこと)は末尾サマリ参照。

戦略仕様(原典 simulator/.../MA_Slope_EA.mq5 / fixture ma_slope_jp225_202501):
  EMA(20, close)、slope = ema[i-1]-ema[i-2]、threshold = slope_min_points*point_size
  = 1.0*0.1 = 0.1。slope>thr→買い / <-thr→売り。反対シグナルでドテン。SL/TP 無し。
  約定は終値プロキシ(日足)。往復コスト COST_POINTS を控除。0.1lot*contract10 = 1JPY/pt。

データ: prototype_260626-01/data.json の '1D'(JP225 日足)を読み取り専用で使用。
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
COST_POINTS = 7.0          # 往復スプレッド(points)。JP225 CFD 想定
EMA_SPAN = 22
SLOPE_SHIFT = 1
THRESHOLD = 1.0 * 0.1      # slope_min_points * point_size


def load_daily():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    close = np.array([x["close"] for x in c], dtype=float)
    return close


def ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def run_ma_strategy(close):
    """MA_Slope を日足で実行し、確定トレード列を返す。
    各トレード = (entry_bar, exit_bar, side(+1/-1), entry_px, exit_px)。常時ポジション・ドテン。
    """
    e = ema(close, EMA_SPAN)
    n = len(close)
    trades = []
    pos = 0          # +1 long / -1 short / 0 flat
    entry_bar = None
    start = 1 + SLOPE_SHIFT
    for i in range(start, n):
        slope = e[i - 1] - e[i - 1 - SLOPE_SHIFT]
        if slope > THRESHOLD:
            sig = 1
        elif slope < -THRESHOLD:
            sig = -1
        else:
            sig = 0
        if sig != 0 and sig != pos:
            if pos != 0:                       # ドテン: 現玉を終値で決済
                trades.append((entry_bar, i, pos, close[entry_bar], close[i]))
            pos = sig
            entry_bar = i
    if pos != 0 and entry_bar is not None and entry_bar < n - 1:
        trades.append((entry_bar, n - 1, pos, close[entry_bar], close[n - 1]))
    return trades


def trade_pnl_pts(entry_px, exit_px, side):
    return (exit_px - entry_px) * side - COST_POINTS


def metrics_from_trades(entry_px, exit_px, side, exit_order_key):
    """確定トレード群から損益統計を計算（points 単位＝0.1lot の JPY）。
    DD は確定損益の累積曲線(イグジット順)のドローダウン。
    """
    pnl = (exit_px - entry_px) * side - COST_POINTS
    order = np.argsort(exit_order_key, kind="stable")
    cum = np.cumsum(pnl[order])
    run_max = np.maximum.accumulate(cum)
    max_dd = float((run_max - cum).max()) if len(cum) else 0.0
    gross_p = float(pnl[pnl > 0].sum())
    gross_l = float(-pnl[pnl < 0].sum())
    pf = gross_p / gross_l if gross_l > 0 else float("inf")
    return {
        "net_pts": float(pnl.sum()),
        "max_dd_pts": max_dd,
        "profit_factor": pf,
        "win_rate": float((pnl > 0).mean()) * 100.0,
        "final_cum": float(cum[-1]) if len(cum) else 0.0,
    }


def run():
    close = load_daily()
    n = len(close)
    rng = np.random.default_rng(SEED)

    trades = run_ma_strategy(close)
    t = np.array(trades, dtype=float)
    s_entry_bar = t[:, 0].astype(int)
    s_exit_bar = t[:, 1].astype(int)
    s_side = t[:, 2].astype(int)
    s_entry_px = t[:, 3]
    s_exit_px = t[:, 4]
    dur = s_exit_bar - s_entry_bar
    N = len(trades)
    n_long = int((s_side > 0).sum())
    n_short = int((s_side < 0).sum())

    strat = metrics_from_trades(s_entry_px, s_exit_px, s_side, s_exit_bar)

    print("=" * 74)
    print("MA_Slope 戦略 vs ランダム（日足・モンテカルロ有意性検定）  JP225")
    print(f"日足 {n}本 / EMA{EMA_SPAN}傾き・ドテン常時ポジ / 往復コスト {COST_POINTS}pt / seed {SEED}")
    print("=" * 74)
    print(f"【戦略 実績】 取引 {N}回 (ロング {n_long} / ショート {n_short})  "
          f"平均保有 {dur.mean():.1f}日")
    print(f"  純損益 {strat['net_pts']:+.0f}pt   最大DD {strat['max_dd_pts']:.0f}pt   "
          f"PF {strat['profit_factor']:.3f}   勝率 {strat['win_rate']:.1f}%")
    print("-" * 74)

    # ---- RND-1: タイミング無効化（保有期間・方向=同一、エントリ時点ランダム） ----
    r1_net = np.empty(M_RUNS)
    r1_dd = np.empty(M_RUNS)
    max_e = n - 1 - dur                     # 各トレードの最大エントリ開始バー
    for m in range(M_RUNS):
        e0 = (rng.random(N) * (max_e + 1)).astype(int)
        e1 = e0 + dur
        mm = metrics_from_trades(close[e0], close[e1], s_side, e1)
        r1_net[m] = mm["net_pts"]
        r1_dd[m] = mm["max_dd_pts"]

    # ---- RND-2: 方向無効化（エントリ/イグジット=同一、方向シャッフル） ----
    r2_net = np.empty(M_RUNS)
    r2_dd = np.empty(M_RUNS)
    for m in range(M_RUNS):
        sd = s_side.copy()
        rng.shuffle(sd)                     # N_long/N_short 不変で方向だけ入替
        mm = metrics_from_trades(s_entry_px, s_exit_px, sd, s_exit_bar)
        r2_net[m] = mm["net_pts"]
        r2_dd[m] = mm["max_dd_pts"]

    def report(tag, net, dd, desc):
        # p_net = ランダムが戦略以上の純損益を出す割合（小さいほど戦略が優秀）
        p_net = float((net >= strat["net_pts"]).mean())
        # p_dd = ランダムが戦略以下のDDで収まる割合（小さいほど戦略のDDが優秀）
        p_dd = float((dd <= strat["max_dd_pts"]).mean())
        pr = lambda a, q: float(np.percentile(a, q))
        print(f"【{tag}】{desc}")
        print(f"  純損益 ランダム分布: 中央 {np.median(net):+.0f}pt  "
              f"[P5 {pr(net,5):+.0f} / P95 {pr(net,95):+.0f}]   "
              f"戦略は上位 {p_net*100:.1f}% (p={p_net:.3f})")
        print(f"  最大DD ランダム分布: 中央 {np.median(dd):.0f}pt  "
              f"[P5 {pr(dd,5):.0f} / P95 {pr(dd,95):.0f}]   "
              f"戦略DDは良い側 {p_dd*100:.1f}% (p={p_dd:.3f})")
        return {"p_net": p_net, "p_dd": p_dd,
                "net_median": float(np.median(net)), "dd_median": float(np.median(dd))}

    print()
    r1 = report("RND-1 タイミング無効化", r1_net, r1_dd,
                "保有期間・方向=戦略と同一、エントリ時点のみランダム")
    print()
    r2 = report("RND-2 方向無効化", r2_net, r2_dd,
                "エントリ/イグジット=戦略と同一、売買方向のみシャッフル")
    print("-" * 74)
    _verdict(strat, r1, r2)

    summary = {
        "asset": "JP225", "bars": n, "cost_points": COST_POINTS, "seed": SEED,
        "m_runs": M_RUNS,
        "strategy": {"trades": N, "n_long": n_long, "n_short": n_short,
                     "avg_hold_days": float(dur.mean()), **strat},
        "rnd1_timing": r1, "rnd2_direction": r2,
    }
    json.dump(summary, open(os.path.join(OUT, "ma_vs_random.json"), "w"),
              ensure_ascii=False, indent=2)
    _plot(strat, r1_net, r1_dd, r2_net, r2_dd)
    print(f"\n出力: {OUT}/ma_vs_random.json , ma_vs_random.png")


def _verdict(strat, r1, r2):
    print("【判定】p<0.05 なら『ランダムでは滅多に出ない=優位性あり』の目安。")
    def line(tag, p):
        v = "有意(優位性あり)" if p < 0.05 else ("弱い" if p < 0.20 else "ランダム並み(優位性なし)")
        print(f"  {tag}: p={p:.3f} → {v}")
    line("入るタイミングの優位性 (RND-1 純損益)", r1["p_net"])
    line("売買方向の優位性     (RND-2 純損益)", r2["p_net"])


def _plot(strat, r1_net, r1_dd, r2_net, r2_dd):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 2, figsize=(13, 8))
    panels = [
        (ax[0][0], r1_net, strat["net_pts"], "RND-1 timing: Net PnL (pts)", False),
        (ax[0][1], r1_dd, strat["max_dd_pts"], "RND-1 timing: Max DD (pts)", True),
        (ax[1][0], r2_net, strat["net_pts"], "RND-2 direction: Net PnL (pts)", False),
        (ax[1][1], r2_dd, strat["max_dd_pts"], "RND-2 direction: Max DD (pts)", True),
    ]
    for a, data, sv, title, dd in panels:
        a.hist(data, bins=60, color="#9bb", alpha=0.7)
        a.axvline(sv, color="#c33", lw=2,
                  label=f"strategy = {sv:,.0f}")
        a.set_title(title)
        a.legend()
    fig.suptitle("MA_Slope strategy (red) vs random distribution (gray) — same trade count & L/S mix",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "ma_vs_random.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
