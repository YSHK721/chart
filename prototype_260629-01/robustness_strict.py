#!/usr/bin/env python3
"""厳格ロバスト性検定（年別・半分割・レジーム別）  JP225 日足

本命シグナル2種を、3つの切り方で更に厳しく検証:
  S1) 終値EMA22 乖離 ≤ -5%
  S2) 安値EMA22 割り込み（(close-ema(low,22))/ema(low,22) ≤ 0）
切り方:
  (A) 年別      : 各暦年の α(=シグナル平均+20d − その年の無条件平均) と勝敗。何年プラスか。
  (B) 半分割    : 前半/後半でα/p。
  (C) レジーム別: 200日SMA基準で 上昇相場(close≥SMA200)/下落相場(close<SMA200) に分け、
                各レジーム内のランダムに対するα/p。←「下落相場でも効くか」が条件付きβの判定打。
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


def load():
    d = json.load(open(DATA))
    c = d["timeframes"]["1D"]["candles"]
    return (np.array([x["time"] for x in c], np.int64),
            np.array([x["low"] for x in c], float),
            np.array([x["close"] for x in c], float))


def ema(x, span):
    a = 2.0 / (span + 1.0)
    o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


def sma(x, w):
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full(len(x), np.nan)
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def fwd(close, idx, H):
    return (close[idx + H] - close[idx]) / close[idx] * 100.0


def p_pool(close, pool, n, sm, rng, M, H):
    """pool(エントリ可能日の配列)から n 個ランダム抽出した平均が sm 以上になる割合。"""
    if len(pool) < n or n == 0:
        return float("nan")
    cnt = 0
    for _ in range(M):
        r = pool[(rng.random(n) * len(pool)).astype(int)]
        if fwd(close, r, H).mean() >= sm:
            cnt += 1
    return cnt / M


def run():
    t, low, close = load()
    n = len(close)
    rng = np.random.default_rng(SEED)
    warm = 200
    hi = n - 1 - H
    years = np.array([dt.datetime.fromtimestamp(int(x), dt.timezone.utc).year for x in t])

    dev_c = (close - ema(close, 22)) / ema(close, 22) * 100.0
    elow = ema(low, 22)
    dev_l = (close - elow) / elow * 100.0
    sma200 = sma(close, 200)

    signals = {
        "S1 終値EMA22 ≤-5%": (dev_c <= -5.0),
        "S2 安値EMA22 割込≤0": (dev_l <= 0.0),
    }
    valid = np.arange(warm, hi + 1)
    out = {}

    for name, sig in signals.items():
        sidx = np.where(sig)[0]
        sidx = sidx[(sidx >= warm) & (sidx <= hi)]
        print("=" * 80)
        print(f"{name}   全 n={len(sidx)}")
        print("=" * 80)

        # (A) 年別
        print("【A】年別 α（無条件比）と勝敗")
        pos = tot = 0
        yr_rows = []
        line = "  "
        for y in range(int(years[warm]), int(years[hi]) + 1):
            ymask_all = (years == y)
            day_all = valid[ymask_all[valid]]
            day_sig = sidx[years[sidx] == y]
            if len(day_sig) < 3 or len(day_all) < 20:
                continue
            a = float(fwd(close, day_sig, H).mean() - fwd(close, day_all, H).mean())
            tot += 1; pos += (a > 0)
            mark = "+" if a > 0 else "-"
            line += f"{y}:{mark}{abs(a):.1f}({len(day_sig)})  "
            yr_rows.append({"year": y, "n": int(len(day_sig)), "alpha": round(a, 3)})
            if len(line) > 95:
                print(line); line = "  "
        if line.strip():
            print(line)
        print(f"  → プラスの年: {pos}/{tot}")

        # (B) 半分割
        mid = warm + (hi - warm) // 2
        print("【B】半分割（前半/後半）")
        half = []
        for lab, lo_b, up_b in [("前半", warm, mid), ("後半", mid, hi + 1)]:
            pool = valid[(valid >= lo_b) & (valid < up_b)]
            sel = sidx[(sidx >= lo_b) & (sidx < up_b)]
            sm = float(fwd(close, sel, H).mean())
            bm = float(fwd(close, pool, H).mean())
            p = p_pool(close, pool, len(sel), sm, rng, M_RUNS, H)
            a = sm - bm
            print(f"  {lab}: n={len(sel):>3}  α {a:+.3f}%  p {p:.3f}  "
                  f"{'再現' if (a>0 and p<0.20) else ('α+弱' if a>0 else 'α≤0')}")
            half.append({"part": lab, "n": int(len(sel)), "alpha": round(a, 3), "p": round(p, 3)})

        # (C) レジーム別
        print("【C】レジーム別（200日SMA基準）")
        reg = []
        up_mask = close >= sma200
        for lab, mask in [("上昇相場(close≥SMA200)", up_mask), ("下落相場(close<SMA200)", ~up_mask)]:
            pool = valid[mask[valid]]
            sel = sidx[mask[sidx]]
            if len(sel) < 5:
                print(f"  {lab}: n={len(sel)} 不足"); continue
            sm = float(fwd(close, sel, H).mean())
            bm = float(fwd(close, pool, H).mean())
            p = p_pool(close, pool, len(sel), sm, rng, M_RUNS, H)
            a = sm - bm
            print(f"  {lab}: n={len(sel):>3}  シグナル平均 {sm:+.2f}%  "
                  f"レジーム無条件 {bm:+.2f}%  α {a:+.3f}%  p {p:.3f}  "
                  f"{'再現' if (a>0 and p<0.20) else ('α+弱' if a>0 else 'α≤0')}")
            reg.append({"regime": lab, "n": int(len(sel)), "sig_mean": round(sm, 3),
                        "base": round(bm, 3), "alpha": round(a, 3), "p": round(p, 3)})
        print()
        out[name] = {"n": int(len(sidx)), "yearly_pos": pos, "yearly_tot": tot,
                     "yearly": yr_rows, "halves": half, "regimes": reg}

    json.dump(out, open(os.path.join(OUT, "robustness_strict.json"), "w"),
              ensure_ascii=False, indent=2)
    print("-" * 80)
    print("レジーム別の下落相場でも α>0・p<0.2 なら『上昇βでなく本物の逆張りエッジ』の強い証拠。")
    print(f"出力: {OUT}/robustness_strict.json")


if __name__ == "__main__":
    run()
