#!/usr/bin/env python3
"""出口ルール比較（入口=ランダム買い固定・出口だけ差し替え）  JP225 日足

問い: 入口はランダムでよい。出口ルールを変えると 最大DD/Ret/DD/期待値 はどう動くか?
入口: 一様ランダムな日にロング（建値=その日の終値）。コスト往復 COST_PTS。
出口（差し替え）:
  A 固定保有20日             … 基準（これまでのやり方）
  B トレーリングストップ5%   … 高値から5%下げたら手仕舞い（利を伸ばす）。上限120日。
  C 固定TP+6%/SL-3%          … 先に触れた方で決済（2:1）。上限120日。日中高安で判定。
  D ボラ連動ストップ(2*ATR14)… 建値-2ATRで損切り、時間上限40日。日中安値で判定。
評価: 各出口で N 取引×M 試行 → 期待値%/勝率/平均保有日/最大DD%/Ret/DD。
      最大DDは確定損益(イグジット順)累積%曲線のドローダウン。1取引=1単位。
データ: prototype_260626-01/data.json '1D' 読み取り専用。
"""
import json
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")

SEED = 20260629
M_RUNS = 2000
N_TRADES = 500
COST_PTS = 7.0
CAP = 120          # 経路スキャン上限(日)
TRAIL = 0.05       # B: 5%
TP, SL = 0.06, 0.03  # C
ATR_K, ATR_CAP = 2.0, 40  # D


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return (np.array([x["high"] for x in c], float),
            np.array([x["low"] for x in c], float),
            np.array([x["close"] for x in c], float))


def atr14(high, low, close):
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]))
    atr = np.empty(n)
    atr[0] = tr[0]
    a = 1.0 / 14
    for i in range(1, n):
        atr[i] = a * tr[i] + (1 - a) * atr[i - 1]
    return atr


def precompute(high, low, close, atr, valid_hi):
    """各エントリ日 i (終値建値) について、出口A〜Dの (return%, exit_bar) を返す。"""
    n = len(close)
    bars = np.arange(0, valid_hi + 1)
    ret = {k: np.empty(len(bars)) for k in "ABCD"}
    ex = {k: np.empty(len(bars), int) for k in "ABCD"}

    for p, i in enumerate(bars):
        entry = close[i]
        cost_pct = COST_PTS / entry * 100.0
        # A: 固定20日
        j = i + 20
        ret["A"][p] = (close[j] - entry) / entry * 100 - cost_pct
        ex["A"][p] = j

        # B: トレーリング5%
        peak = high[i]
        jb = i + CAP
        for j in range(i + 1, i + CAP + 1):
            peak = max(peak, high[j])
            stop = peak * (1 - TRAIL)
            if low[j] <= stop:
                ret["B"][p] = (stop - entry) / entry * 100 - cost_pct
                jb = j
                break
        else:
            ret["B"][p] = (close[i + CAP] - entry) / entry * 100 - cost_pct
        ex["B"][p] = jb

        # C: TP+6%/SL-3%（同日両触れはSL優先）
        tp_lv, sl_lv = entry * (1 + TP), entry * (1 - SL)
        jc = i + CAP
        done = False
        for j in range(i + 1, i + CAP + 1):
            if low[j] <= sl_lv:
                ret["C"][p] = -SL * 100 - cost_pct
                jc = j; done = True; break
            if high[j] >= tp_lv:
                ret["C"][p] = TP * 100 - cost_pct
                jc = j; done = True; break
        if not done:
            ret["C"][p] = (close[i + CAP] - entry) / entry * 100 - cost_pct
        ex["C"][p] = jc

        # D: 2*ATR ストップ + 時間40日
        sl_lv = entry - ATR_K * atr[i]
        jd = i + ATR_CAP
        done = False
        for j in range(i + 1, i + ATR_CAP + 1):
            if low[j] <= sl_lv:
                ret["D"][p] = (sl_lv - entry) / entry * 100 - cost_pct
                jd = j; done = True; break
        if not done:
            ret["D"][p] = (close[i + ATR_CAP] - entry) / entry * 100 - cost_pct
        ex["D"][p] = jd

    hold = {k: (ex[k] - bars) for k in "ABCD"}
    return ret, ex, hold


def mc(ret, ex, hold, n_bars_valid, rng, M, N):
    out = {}
    for k in "ABCD":
        r, e, h = ret[k], ex[k], hold[k]
        ev = np.empty(M); dd = np.empty(M); rdd = np.empty(M)
        for m in range(M):
            sel = (rng.random(N) * len(r)).astype(int)
            pnl = r[sel]
            order = np.argsort(e[sel], kind="stable")
            cum = np.cumsum(pnl[order])
            run_max = np.maximum.accumulate(cum)
            mdd = (run_max - cum).max()
            ev[m] = pnl.mean()
            dd[m] = mdd
            rdd[m] = cum[-1] / mdd if mdd > 0 else np.inf
        rf = rdd[np.isfinite(rdd)]
        out[k] = {
            "ev_per_trade": float(np.median(ev)),
            "win_rate": float((r > 0).mean()) * 100,
            "avg_hold": float(h.mean()),
            "total_ret_median": float(np.median(ev) * N),
            "max_dd_median": float(np.median(dd)),
            "max_dd_p95": float(np.percentile(dd, 95)),
            "ret_dd_median": float(np.median(rf)),
        }
    return out


def run():
    high, low, close = load()
    n = len(close)
    rng = np.random.default_rng(SEED)
    atr = atr14(high, low, close)
    valid_hi = n - 1 - CAP

    ret, ex, hold = precompute(high, low, close, atr, valid_hi)
    res = mc(ret, ex, hold, len(ret["A"]), rng, M_RUNS, N_TRADES)

    names = {"A": "固定20日(基準)", "B": "トレール5%", "C": "TP6%/SL3%", "D": "2ATRストップ"}
    print("=" * 86)
    print("出口ルール比較（入口=ランダム買い固定）  JP225 日足")
    print(f"N={N_TRADES}取引 × M={M_RUNS}試行 / コスト{COST_PTS}pt / seed {SEED}")
    print("=" * 86)
    print(f"{'出口':>16} {'期待値%/取引':>12} {'勝率':>7} {'平均保有日':>9} "
          f"{'総Ret%':>8} {'最大DD%':>8} {'DD(P95)':>8} {'Ret/DD':>8}")
    print("-" * 86)
    for k in "ABCD":
        r = res[k]
        print(f"{names[k]:>16} {r['ev_per_trade']:11.3f} {r['win_rate']:6.1f}% "
              f"{r['avg_hold']:8.1f} {r['total_ret_median']:7.0f} "
              f"{r['max_dd_median']:7.0f} {r['max_dd_p95']:7.0f} {r['ret_dd_median']:7.2f}")
    print("-" * 86)
    print("入口は全て同じランダム買い。差は出口だけ。Ret/DD と 最大DD の改善に注目。")

    summary = {"n_trades": N_TRADES, "m_runs": M_RUNS, "cost_pts": COST_PTS,
               "params": {"trail": TRAIL, "tp": TP, "sl": SL, "atr_k": ATR_K},
               "exits": {names[k]: res[k] for k in "ABCD"}}
    json.dump(summary, open(os.path.join(OUT, "exit_rules.json"), "w"),
              ensure_ascii=False, indent=2)
    _plot(res, names)
    print(f"\n出力: {OUT}/exit_rules.json , exit_rules.png")


def _plot(res, names):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ks = list("ABCD")
    labels = [names[k] for k in ks]
    dd = [res[k]["max_dd_median"] for k in ks]
    rdd = [res[k]["ret_dd_median"] for k in ks]
    ev = [res[k]["ev_per_trade"] for k in ks]
    fig, ax = plt.subplots(1, 3, figsize=(14, 4.5))
    en = ["A:hold20", "B:trail5%", "C:TP6/SL3", "D:2ATR"]
    ax[0].bar(en, dd, color="#c66"); ax[0].set_title("Max DD % (median, lower=better)")
    ax[1].bar(en, rdd, color="#6a6"); ax[1].set_title("Ret/DD (higher=better)")
    ax[2].bar(en, ev, color="#69c"); ax[2].set_title("Expected value % / trade")
    for a in ax:
        a.axhline(0, color="#999", lw=0.6); a.tick_params(axis='x', rotation=15)
    fig.suptitle("Exit rule comparison — same random LONG entry, only exit differs", fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "exit_rules.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
