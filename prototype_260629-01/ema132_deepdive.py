#!/usr/bin/env python3
"""EMA132 押し目買い 深掘り検証（全期間 + サブ期間ロバスト性 + ホライズン + 同数ランダム）

問い: 唯一信号候補の EMA132 深い押し目は「本物のエッジ」か「偶然(偏った期間の産物)」か。
判定軸:
  1) 全期間: 平均+20d、勝率、無条件との α、同数ランダムに対する p。
  2) ロバスト性(最重要): 期間を3分割し、各期で α>0 と p が再現するか。
     一部の期間だけなら過剰適合/偶然。全期で残れば本物寄り。
  3) ホライズン: +5/+10/+20/+40d の平均と速度(%/日)。
  4) DD近似: +20d・1単位・%建値比の累積曲線で最大DDとRet/DD。
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
L = 132
HORIZONS = [5, 10, 20, 40]


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return (np.array([x["time"] for x in c], np.int64),
            np.array([x["open"] for x in c], float),
            np.array([x["low"] for x in c], float),
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


def p_random(close, lo, hi_excl, n, strat_mean, H, rng, M):
    """[lo,hi_excl) の範囲で n 日ランダム買い+H、平均が strat_mean 以上になる割合。"""
    rng_hi = hi_excl - 1
    cnt = 0
    for _ in range(M):
        r = lo + (rng.random(n) * (rng_hi - lo + 1)).astype(int)
        if fwd(close, r, H).mean() >= strat_mean:
            cnt += 1
    return cnt / M


def dd_metrics(pnl_pct, exit_bar):
    order = np.argsort(exit_bar, kind="stable")
    cum = np.cumsum(pnl_pct[order])
    run_max = np.maximum.accumulate(cum)
    max_dd = float((run_max - cum).max())
    ret = float(cum[-1])
    return ret, max_dd, (ret / max_dd if max_dd > 0 else float("inf"))


def run():
    t, o, low, cl = load()
    n_bars = len(cl)
    rng = np.random.default_rng(SEED)
    e = ema(cl, L)
    touch = (o >= e) & (low <= e)
    H20 = 20
    hi = n_bars - 1 - max(HORIZONS)
    idx_all = np.where(touch)[0]
    idx_all = idx_all[idx_all <= hi]

    def ymd(ts):
        return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date()

    print("=" * 80)
    print(f"EMA{L} 押し目買い 深掘り検証  JP225 日足  M={M_RUNS}  seed {SEED}")
    print(f"期間 {ymd(t[0])}〜{ymd(t[hi])}   接触押し目 n={len(idx_all)}")
    print("=" * 80)

    # --- 1) 全期間 ---
    base20 = fwd(cl, np.arange(0, hi + 1), H20)
    base_mean = float(base20.mean())
    s20 = fwd(cl, idx_all, H20)
    s_mean = float(s20.mean())
    s_win = float((s20 > 0).mean()) * 100
    p_full = p_random(cl, 0, hi + 1, len(idx_all), s_mean, H20, rng, M_RUNS)
    ret, dd, retdd = dd_metrics(s20, idx_all + H20)
    print("【1】全期間 +20d")
    print(f"  平均 {s_mean:+.3f}%  勝率 {s_win:.1f}%  無条件 {base_mean:+.3f}%  "
          f"α {s_mean-base_mean:+.3f}%  p(乱≥押) {p_full:.3f}")
    print(f"  累積Ret {ret:+.0f}%  最大DD {dd:.0f}%  Ret/DD {retdd:.2f}")

    # --- 2) サブ期間ロバスト性(3分割) ---
    print("\n【2】サブ期間ロバスト性（時間3分割：各期で α>0 と p が再現するか）")
    print(f"  {'期間':>21} {'n':>4} {'平均%':>8} {'無条件%':>8} {'α':>8} {'p(乱≥押)':>9} {'判定':>10}")
    bounds = [0, hi // 3, 2 * hi // 3, hi + 1]
    sub = []
    for k in range(3):
        lo, up = bounds[k], bounds[k + 1]
        sel = idx_all[(idx_all >= lo) & (idx_all < up)]
        if len(sel) < 10:
            print(f"  {ymd(t[lo])}〜{ymd(t[up-1])}  サンプル不足")
            continue
        bm = float(fwd(cl, np.arange(lo, up), H20).mean())
        sm = float(fwd(cl, sel, H20).mean())
        pk = p_random(cl, lo, up, len(sel), sm, H20, rng, M_RUNS)
        a = sm - bm
        verdict = "α+ 再現" if (a > 0 and pk < 0.20) else ("α+ 弱" if a > 0 else "α≤0")
        print(f"  {str(ymd(t[lo]))}〜{str(ymd(t[up-1]))} {len(sel):>4} "
              f"{sm:7.3f}% {bm:7.3f}% {a:+7.3f}% {pk:8.3f} {verdict:>10}")
        sub.append({"from": str(ymd(t[lo])), "to": str(ymd(t[up - 1])),
                    "n": int(len(sel)), "mean": round(sm, 4), "base": round(bm, 4),
                    "alpha": round(a, 4), "p": round(pk, 4), "verdict": verdict})

    # --- 3) ホライズン別 速度 ---
    print("\n【3】ホライズン別（押し目 vs 無条件、速度=%/日）")
    print(f"  {'H':>4} {'押し目平均%':>11} {'速度%/日':>9} {'無条件%':>9} {'無条速度':>9}")
    horizons = {}
    for H in HORIZONS:
        hi_h = n_bars - 1 - H
        sidx = idx_all[idx_all <= hi_h]
        sm = float(fwd(cl, sidx, H).mean())
        bm = float(fwd(cl, np.arange(0, hi_h + 1), H).mean())
        print(f"  {H:>4} {sm:10.3f}% {sm/H:8.4f} {bm:8.3f}% {bm/H:8.4f}")
        horizons[H] = {"strat_mean": round(sm, 4), "strat_speed": round(sm / H, 4),
                       "base_mean": round(bm, 4), "base_speed": round(bm / H, 4)}

    print("-" * 80)
    _verdict(sub)

    summary = {"length": L, "n": int(len(idx_all)), "m_runs": M_RUNS, "seed": SEED,
               "full": {"mean": round(s_mean, 4), "win": round(s_win, 2),
                        "base_mean": round(base_mean, 4),
                        "alpha": round(s_mean - base_mean, 4), "p": round(p_full, 4),
                        "ret": round(ret, 2), "max_dd": round(dd, 2),
                        "ret_dd": round(retdd, 2)},
               "subperiods": sub, "horizons": horizons}
    json.dump(summary, open(os.path.join(OUT, "ema132_deepdive.json"), "w"),
              ensure_ascii=False, indent=2)
    print(f"\n出力: {OUT}/ema132_deepdive.json")


def _verdict(sub):
    pos = [s for s in sub if s["alpha"] > 0]
    strong = [s for s in sub if s["verdict"] == "α+ 再現"]
    print("【総合判定】")
    print(f"  3期中 α>0: {len(pos)}/{len(sub)}期   α+再現(p<0.2): {len(strong)}/{len(sub)}期")
    if len(strong) >= 2:
        print("  → 複数期で再現 = エッジの可能性。次はアウトオブサンプル(別銘柄/別期間)で確認。")
    elif len(pos) <= 1:
        print("  → 1期以下に集中 = 偶然/過剰適合の疑い濃厚。EMA132もエッジとは言えない。")
    else:
        print("  → 部分的にのみ出現 = 不安定。エッジ断定は不可。")


if __name__ == "__main__":
    run()
