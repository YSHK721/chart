"""値幅の分位による閾値を、事前登録（RESULTS.md §1）どおりに測る。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_quantile.run
出力: tools/measure/tr_quantile/results/{rates.csv, verdict.csv}
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.tr_quantile import tq as Q

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TFS = ("15m", "1h", "4h", "1D")
B = 1999
SEED = 20261027
TOL = 2.0
MIN_N = 200
PERIODS = {
    "IS": ("data/marketdata/rollups/jp225_tick_bid/jp225_tick_bid_{tf}.csv", "2014-01-01", "2020-05-01"),
    "OOS": ("data/marketdata/rollups/jp225_mt5/jp225_mt5_{tf}.csv", "2020-05-01", "2100-01-01"),
}
OUT = HERE / "results"


def load(period, tf):
    path, start, end = PERIODS[period]
    d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "high", "low", "close"], parse_dates=["date"]).iloc[:-1]
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5]
    d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
    y, v, r, ok = Q.features(d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float))
    days = d.date[ok].dt.normalize()
    day = pd.Index(days.unique()).get_indexer(days)
    return y, v, r, day


def main():
    OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED)
    rows = []
    for tf in TFS:
        yi, vi, ri, _ = load("IS", tf)
        yo, vo, ro, day = load("OOS", tf)
        edges = np.quantile(vi, [0.2, 0.4, 0.6, 0.8])
        bucket = np.digitize(vo, edges)
        nd = int(day.max()) + 1
        w = rng.multinomial(nd, np.full(nd, 1 / nd), size=B).astype(float)
        cnt_day = np.bincount(day, minlength=nd).astype(float)
        for q in Q.QS:
            target = (1 - q) * 100
            for m in Q.METHODS:
                p = Q.fit(m, yi, vi, ri, q, edges)
                th = Q.threshold(m, p, vo, ro, edges)
                ex = (yo > th).astype(float)
                ex_day = np.bincount(day, weights=ex, minlength=nd)
                boot = (w @ ex_day) / (w @ cnt_day) * 100
                rows.append(dict(tf=tf, q=q, target=target, method=m, group="全体", n=len(yo),
                                 rate=ex.mean() * 100, ci_lo=float(np.quantile(boot, .025)),
                                 ci_hi=float(np.quantile(boot, .975)), mean_th=float(np.nanmean(th))))
                for k in range(5):
                    mk = bucket == k
                    rows.append(dict(tf=tf, q=q, target=target, method=m, group=f"区分{k + 1}", n=int(mk.sum()),
                                     rate=ex[mk].mean() * 100 if mk.any() else np.nan, ci_lo=np.nan, ci_hi=np.nan,
                                     mean_th=float(np.nanmean(th[mk])) if mk.any() else np.nan))
        print(tf, "done", flush=True)
    t = pd.DataFrame(rows)
    t["err"] = t.rate - t.target
    t.to_csv(OUT / "rates.csv", index=False)
    judged = t[t.n >= MIN_N]
    v = judged.groupby(["tf", "q", "method"]).agg(max_abs_err=("err", lambda s: s.abs().max())).reset_index()
    v["pass"] = v.max_abs_err <= TOL
    v.to_csv(OUT / "verdict.csv", index=False)
    pd.set_option("display.width", 220)
    print(v.round(2).to_string(index=False))
    print("合格", int(v["pass"].sum()), "/", len(v))


if __name__ == "__main__":
    main()
