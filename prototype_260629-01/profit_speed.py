#!/usr/bin/env python3
"""利益発生の速度（エントリ後のリターン軌跡の傾き）  押し目買い vs ランダム vs 無条件

問い: 「利益が発生する速度は計測できるか?」→ できる。
定義: 速度 = エントリ後 k 日の平均リターン軌跡 R(k) の傾き(%/日)。
      R(k) = 各エントリで (close[i+k]/close[i]-1)*100 の平均。
      ・全区間速度 = R(H)/H
      ・初動速度   = R(5)/5（買った直後にどれだけ速く乗るか）
      ・ピーク日   = R(k) が最大になる k（利益が頭打ちになる時点）
比較: 押し目買い(各EMA長) / ランダム買い / 無条件(全日買い)。

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
KMAX = 40           # 軌跡を見る日数
EMA_LENS = [5, 22, 66, 132, 259]


def load_daily():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return (np.array([x["open"] for x in c], float),
            np.array([x["low"] for x in c], float),
            np.array([x["close"] for x in c], float))


def ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def trajectory(close, idx, kmax):
    """エントリ群 idx について k=0..kmax の平均リターン軌跡(%)を返す。"""
    R = np.empty(kmax + 1)
    for k in range(kmax + 1):
        R[k] = ((close[idx + k] - close[idx]) / close[idx] * 100.0).mean()
    return R


def speeds(R):
    h20 = 20
    return {
        "speed_per_day_20d": R[h20] / h20,        # 全区間速度(%/日)
        "speed_per_day_5d": R[5] / 5.0,           # 初動速度(%/日)
        "ret_5d": R[5], "ret_20d": R[20], "ret_40d": R[len(R) - 1],
        "peak_day": int(np.argmax(R)),
        "peak_ret": float(R.max()),
    }


def run():
    o, low, cl = load_daily()
    n = len(cl)
    rng = np.random.default_rng(SEED)
    hi = n - 1 - KMAX

    print("=" * 84)
    print("利益発生の速度（エントリ後リターン軌跡の傾き）  JP225 日足")
    print(f"日足 {n}本 / 軌跡 0..{KMAX}日 / seed {SEED}")
    print("=" * 84)

    # 無条件(全日買い)
    all_idx = np.arange(0, hi + 1)
    R_base = trajectory(cl, all_idx, KMAX)
    # ランダム(無条件と同じ母集団なので軌跡は base と一致。表記のため別掲)
    sb = speeds(R_base)

    rows = {}
    print(f"{'対象':>14} {'n':>6} {'初動%/日(5d)':>13} {'全速%/日(20d)':>14} "
          f"{'5d%':>7} {'20d%':>7} {'40d%':>7} {'ピーク日':>8}")
    print("-" * 84)
    print(f"{'無条件/ランダム':>14} {len(all_idx):>6} {sb['speed_per_day_5d']:12.4f} "
          f"{sb['speed_per_day_20d']:13.4f} {sb['ret_5d']:6.2f} {sb['ret_20d']:6.2f} "
          f"{sb['ret_40d']:6.2f} {sb['peak_day']:7d}")
    rows["baseline_random"] = {"n": int(len(all_idx)), **sb,
                               "trajectory": [round(x, 4) for x in R_base]}

    for L in EMA_LENS:
        e = ema(cl, L)
        touch = (o >= e) & (low <= e)
        idx = np.where(touch)[0]
        idx = idx[idx <= hi]
        if len(idx) < 10:
            continue
        R = trajectory(cl, idx, KMAX)
        s = speeds(R)
        # 初動速度が無条件より速いか(初動5d速度差)
        d5 = s["speed_per_day_5d"] - sb["speed_per_day_5d"]
        tag = f"押し目 EMA{L}"
        print(f"{tag:>14} {len(idx):>6} {s['speed_per_day_5d']:12.4f} "
              f"{s['speed_per_day_20d']:13.4f} {s['ret_5d']:6.2f} {s['ret_20d']:6.2f} "
              f"{s['ret_40d']:6.2f} {s['peak_day']:7d}")
        rows[f"ema{L}"] = {"n": int(len(idx)), "init_speed_minus_baseline": round(d5, 4),
                           **s, "trajectory": [round(x, 4) for x in R]}

    print("-" * 84)
    print("初動%/日 = 買った直後5日の平均リターン傾き。これが無条件(ランダム)より")
    print("大きければ『押し目は利益が乗るのが速い』。小さければ『直後はむしろ含み損で遅い』。")

    summary = {"kmax": KMAX, "seed": SEED, "rows": rows}
    json.dump(summary, open(os.path.join(OUT, "profit_speed.json"), "w"),
              ensure_ascii=False, indent=2)
    _plot(rows)
    print(f"\n出力: {OUT}/profit_speed.json , profit_speed.png")


def _plot(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 6))
    k = np.arange(0, len(rows["baseline_random"]["trajectory"]))
    ax.plot(k, rows["baseline_random"]["trajectory"], "k--", lw=2.2,
            label="baseline / random (all days)")
    colors = {"ema5": "#888", "ema22": "#2a7", "ema66": "#e90",
              "ema132": "#36c", "ema259": "#c33"}
    for key, col in colors.items():
        if key in rows:
            ax.plot(k, rows[key]["trajectory"], color=col, lw=1.6,
                    label=f"pullback EMA{key[3:]} (n={rows[key]['n']})")
    ax.axhline(0, color="#999", lw=0.6)
    ax.set_title("Profit accrual speed: avg return path after entry (JP225 daily)")
    ax.set_xlabel("days held after entry")
    ax.set_ylabel("mean cumulative return %")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "profit_speed.png"), dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    run()
