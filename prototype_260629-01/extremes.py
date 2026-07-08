#!/usr/bin/env python3
"""底/天井に意味はあるか（後知恵の極値 vs リアルタイム検出可能な条件 vs ランダム）

問い: 「底や天井に意味は無いのか?」
分解:
  (1) 後知恵の極値(未来参照・実取引不可): ±k窓の最安値=底/最高値=天井で買った場合の+20d。
      → 「完璧に底/天井を当てられたら」の価値の上限。構造の有無を示す。
  (2) リアルタイム条件(過去のみ・実取引可): これらで買った場合の+20d。
      ・新20日安値(close=過去20日最安) … 押し目/落ちるナイフ
      ・3日連続陰線               … 短期売られすぎ
      ・60日高値から-10%以上下落    … 押し幅
      ・新20日高値(過去20日最高)    … ブレイク/モメンタム
  各条件の平均+20d を、同数 n のランダム買い分布と比較(p値)。p<0.05 で「ランダム超=意味あり」。
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
H = 20
K = 20   # 後知恵極値の窓(±K)


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return np.array([x["close"] for x in c], float)


def fwd(close, idx, H):
    return (close[idx + H] - close[idx]) / close[idx] * 100.0


def p_vs_random(close, idx_pool_hi, n, strat_mean, rng, M, H):
    cnt = 0
    for _ in range(M):
        r = (rng.random(n) * (idx_pool_hi + 1)).astype(int)
        if fwd(close, r, H).mean() >= strat_mean:
            cnt += 1
    return cnt / M


def run():
    close = load()
    n = len(close)
    rng = np.random.default_rng(SEED)
    hi = n - 1 - H                      # +H が引ける最大エントリ
    base = float(fwd(close, np.arange(0, hi + 1), H).mean())

    print("=" * 86)
    print("底/天井に意味はあるか  JP225 日足  +%dd / M=%d / seed %d" % (H, M_RUNS, SEED))
    print(f"基準: 無条件(ランダム)買い +{H}d 平均 {base:+.3f}%")
    print("=" * 86)
    print(f"{'条件':>26} {'種別':>10} {'n':>5} {'平均+20d%':>10} {'勝率':>7} "
          f"{'vs乱α':>8} {'p(乱≥)':>8} {'判定':>10}")
    print("-" * 86)

    rows = []

    def emit(name, kind, idx, hindsight=False):
        idx = idx[(idx >= 0) & (idx <= hi)]
        if len(idx) < 10:
            return
        m = float(fwd(close, idx, H).mean())
        win = float((fwd(close, idx, H) > 0).mean()) * 100
        if hindsight:
            p = float("nan"); verdict = "未来参照(上限)"
        else:
            p = p_vs_random(close, hi, len(idx), m, rng, M_RUNS, H)
            verdict = "意味あり" if p < 0.05 else ("弱い" if p < 0.20 else "ランダム並み")
        ps = " n/a " if hindsight else f"{p:.3f}"
        print(f"{name:>26} {kind:>10} {len(idx):>5} {m:9.3f}% {win:6.1f}% "
              f"{m-base:+7.3f}% {ps:>8} {verdict:>10}")
        rows.append({"name": name, "kind": kind, "n": int(len(idx)),
                     "mean20d": round(m, 4), "win": round(win, 2),
                     "alpha_vs_random": round(m - base, 4),
                     "p": (None if hindsight else round(p, 4)), "verdict": verdict})

    # (1) 後知恵の極値（未来参照）
    is_bottom = np.zeros(n, bool); is_top = np.zeros(n, bool)
    for i in range(K, n - K):
        w = close[i - K:i + K + 1]
        if close[i] == w.min():
            is_bottom[i] = True
        if close[i] == w.max():
            is_top[i] = True
    emit(f"後知恵の底(±{K}日最安)", "後知恵", np.where(is_bottom)[0], hindsight=True)
    emit(f"後知恵の天井(±{K}日最高)", "後知恵", np.where(is_top)[0], hindsight=True)

    # (2) リアルタイム条件（過去のみ）
    # 新20日安値: close[i] <= 過去20日(i-20..i-1)の最安
    nl = np.zeros(n, bool); nh = np.zeros(n, bool)
    for i in range(20, n):
        past = close[i - 20:i]
        if close[i] <= past.min():
            nl[i] = True
        if close[i] >= past.max():
            nh[i] = True
    emit("新20日安値で買い", "実取引可", np.where(nl)[0])
    emit("新20日高値で買い", "実取引可", np.where(nh)[0])

    # 3日連続下落
    down3 = np.zeros(n, bool)
    for i in range(3, n):
        if close[i] < close[i-1] < close[i-2] < close[i-3]:
            down3[i] = True
    emit("3日連続陰線で買い", "実取引可", np.where(down3)[0])

    # 60日高値から-10%以上
    dd10 = np.zeros(n, bool)
    for i in range(60, n):
        hi60 = close[i - 60:i].max()
        if close[i] <= hi60 * 0.90:
            dd10[i] = True
    emit("60日高値-10%押しで買い", "実取引可", np.where(dd10)[0])

    print("-" * 86)
    print("後知恵=未来を見て極値を当てた場合(取引不可)。実取引可=過去のみで判定。")
    print("p>=0.05 → その『底/天井っぽさ』はランダム買いと区別できない=実用上の意味なし。")

    json.dump({"horizon": H, "baseline": round(base, 4), "rows": rows},
              open(os.path.join(OUT, "extremes.json"), "w"), ensure_ascii=False, indent=2)
    print(f"\n出力: {OUT}/extremes.json")


if __name__ == "__main__":
    run()
