"""水準に届く確率を、事前登録（RESULTS.md §1）どおりに測る。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.reach_prob.run
出力: tools/measure/reach_prob/results/{table.csv, verdict.csv}
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.reach_prob import reach as R

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TFS = ("15m", "1h", "4h", "1D")
B = 1999
SEED = 20261026
TOL = 0.03
MIN_N = 200
PERIODS = {
    "IS": ("data/marketdata/rollups/jp225_tick_bid/jp225_tick_bid_{tf}.csv", "2014-01-01", "2020-05-01"),
    "OOS": ("data/marketdata/rollups/jp225_mt5/jp225_mt5_{tf}.csv", "2020-05-01", "2100-01-01"),
}
OUT = HERE / "results"


def counts(period, tf):
    path, start, end = PERIODS[period]
    d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "high", "low", "close"], parse_dates=["date"]).iloc[:-1]
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5].reset_index(drop=True)
    inp = ((d.date >= start) & (d.date < end)).to_numpy()
    # 期間の外の足は窓に入れない（期間の足だけで計算する）
    d = d[inp].reset_index(drop=True)
    days = d.date.dt.normalize()
    day = pd.Index(days.unique()).get_indexer(days)
    start_ok = np.arange(len(d)) >= R.WINDOW
    return R.reach_counts(d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float),
                          start_ok, day, int(day.max()) + 1)


def main():
    OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED)
    rows = []
    for tf in TFS:
        cis, coos = counts("IS", tf), counts("OOS", tf)
        for (t, direction), (n_o, hit_o, f_o) in coos.items():
            n_i, hit_i, f_i = cis[(t, direction)]
            nd = len(n_o)
            w = rng.multinomial(nd, np.full(nd, 1 / nd), size=B).astype(float)
            for k, x in enumerate(R.XS):
                p_is = hit_i[k].sum() / n_i.sum()
                p_oos = hit_o[k].sum() / n_o.sum()
                boot = (w @ hit_o[k]) / (w @ n_o) - p_is
                rows.append(dict(tf=tf, T=t, dir=direction, x=x, is_n=int(n_i.sum()), is_p=p_is,
                                 oos_n=int(n_o.sum()), oos_p=p_oos, diff=p_oos - p_is,
                                 ci_lo=float(np.quantile(boot, .025)), ci_hi=float(np.quantile(boot, .975)),
                                 formula_oos=f_o[k].sum() / n_o.sum(),
                                 formula_diff=p_oos - f_o[k].sum() / n_o.sum(),
                                 oos_hits=int(hit_o[k].sum())))
        print(tf, "done", flush=True)
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "table.csv", index=False)
    ok = t[t.oos_n >= MIN_N]
    v = ok.groupby(["tf", "T", "dir"]).agg(table_max_err=("diff", lambda s: s.abs().max()),
                                           formula_max_err=("formula_diff", lambda s: s.abs().max())).reset_index()
    v["table_pass"] = v.table_max_err <= TOL
    v["formula_pass"] = v.formula_max_err <= TOL
    v.to_csv(OUT / "verdict.csv", index=False)
    pd.set_option("display.width", 200)
    print(v.round(4).to_string(index=False))
    print("表 合格", int(v.table_pass.sum()), "/", len(v), "・式 合格", int(v.formula_pass.sum()), "/", len(v))


if __name__ == "__main__":
    main()
