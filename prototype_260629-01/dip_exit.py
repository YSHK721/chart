#!/usr/bin/env python3
"""下方乖離の逆張り買いに対する「ロスカット（時間ストップ等）」の検証  JP225 日足

入口: 終値EMA22 乖離 ≤ TH%(深い売られすぎ)で買い（建値=その日の終値）。
出口比較:
  R0 基準        : ストップなし、20日固定保有。
  R1 EMA回帰/時間 : 「EMA水準」へ戻ったら利確。n日以内に戻らなければ時間ストップで決済。
  R2 同値/時間    : 「建値(±0)」へ戻ったら手仕舞い。n日以内に戻らなければ時間ストップ。
  R3 ハードSL     : -X% で損切り、無ければ20日保有。
狙い: 逆張りは「すぐ戻る」が前提。戻らない玉(=前提崩れ)を時間で切ると Ret/DD・最大DD が改善するか。
評価: 期待値%/取引・勝率・平均保有日・最大DD%(確定損益累積)・Ret/DD。
データ: prototype_260626-01/data.json '1D' 読み取り専用。
"""
import json
import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "prototype_260626-01", "data.json")
OUT = os.path.join(HERE, "out")

SEED = 20260629
SPAN = 22
TH = -3.0          # 入口: 乖離 ≤ -3%
COST = 0.0         # 比較のためコスト0（相対評価）
CAP = 60           # 経路上限
HOLD = 20
HARD_SL = 0.05
DEADLINES = [5, 10, 20]


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return (np.array([x["high"] for x in c], float),
            np.array([x["low"] for x in c], float),
            np.array([x["close"] for x in c], float))


def ema(x, span):
    a = 2.0 / (span + 1.0)
    o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


def metrics(rets, exits):
    rets = np.asarray(rets); exits = np.asarray(exits)
    order = np.argsort(exits, kind="stable")
    cum = np.cumsum(rets[order])
    run = np.maximum.accumulate(cum)
    dd = float((run - cum).max()) if len(cum) else 0.0
    return {"ev": float(rets.mean()), "win": float((rets > 0).mean()) * 100,
            "total": float(cum[-1]) if len(cum) else 0.0, "max_dd": dd,
            "ret_dd": (float(cum[-1]) / dd if dd > 0 else float("inf"))}


def run():
    high, low, close = load()
    n = len(close)
    e = ema(close, SPAN)
    dev = (close - e) / e * 100.0
    warm = SPAN * 3
    hi = n - 1 - CAP
    sig = np.where(dev <= TH)[0]
    sig = sig[(sig >= warm) & (sig <= hi)]

    def hold20(i):
        entry = close[i]
        return (close[i + HOLD] - entry) / entry * 100 - COST, i + HOLD

    def target_time(i, target, n_dead):
        entry = close[i]
        for j in range(1, n_dead + 1):
            if high[i + j] >= target:                 # 目標(EMA or 建値)へ回帰→利確/手仕舞い
                return (target - entry) / entry * 100 - COST, i + j
        return (close[i + n_dead] - entry) / entry * 100 - COST, i + n_dead  # 時間ストップ

    def hard_sl(i, x):
        entry = close[i]; sl = entry * (1 - x)
        for j in range(1, HOLD + 1):
            if low[i + j] <= sl:
                return -x * 100 - COST, i + j
        return (close[i + HOLD] - entry) / entry * 100 - COST, i + HOLD

    print("=" * 88)
    print(f"下方乖離≤{TH:g}% の逆張り買い × ロスカット出口比較  JP225 日足  n={len(sig)}")
    print("=" * 88)
    print(f"{'出口ルール':>26} {'期待値%':>8} {'勝率':>7} {'平均保有':>8} "
          f"{'総Ret%':>8} {'最大DD%':>8} {'Ret/DD':>8}")
    print("-" * 88)
    results = {}

    def show(label, fn):
        rs = []; ex = []
        for i in sig:
            r, xb = fn(i)
            rs.append(r); ex.append(xb)
        m = metrics(rs, ex)
        hold = float(np.mean([x - i for i, x in zip(sig, ex)]))
        print(f"{label:>26} {m['ev']:7.3f} {m['win']:6.1f}% {hold:7.1f} "
              f"{m['total']:7.0f} {m['max_dd']:7.0f} {m['ret_dd']:7.2f}")
        results[label] = {**m, "avg_hold": hold}

    show("R0 基準(20日保有)", hold20)
    for nd in DEADLINES:
        show(f"R1 EMA回帰/{nd}日時間SL", lambda i, nd=nd: target_time(i, e[i], nd))
    for nd in DEADLINES:
        show(f"R2 建値回帰/{nd}日時間SL", lambda i, nd=nd: target_time(i, close[i], nd))
    show(f"R3 ハードSL -{int(HARD_SL*100)}%", lambda i: hard_sl(i, HARD_SL))

    print("-" * 88)
    print("逆張りは『すぐ戻る』が前提。戻らない玉を時間で切ると最大DD・Ret/DDが改善するか比較。")
    json.dump({"signal": f"dev<={TH}", "n": int(len(sig)), "results": results},
              open(os.path.join(OUT, "dip_exit.json"), "w"), ensure_ascii=False, indent=2)
    _plot(results)
    print(f"\n出力: {OUT}/dip_exit.json , dip_exit.png")


def _plot(results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = list(results.keys())
    short = [l.split()[0] for l in labels]
    dd = [results[l]["max_dd"] for l in labels]
    rdd = [results[l]["ret_dd"] for l in labels]
    ev = [results[l]["ev"] for l in labels]
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.6))
    ax[0].bar(short, dd, color="#c66"); ax[0].set_title("Max DD % (lower=better)")
    ax[1].bar(short, rdd, color="#6a6"); ax[1].set_title("Ret/DD (higher=better)")
    ax[2].bar(short, ev, color="#69c"); ax[2].set_title("Expected value % / trade")
    for a in ax:
        a.axhline(0, color="#999", lw=0.6); a.tick_params(axis='x', rotation=40, labelsize=8)
    fig.suptitle(f"Dip-buy (dev<={TH:g}%) exit/stop comparison — JP225 daily", fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "dip_exit.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
