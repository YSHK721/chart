"""荒れ方の見積もりを混ぜる方法（E・F）を A〜D と同じ足にそろえて測る（RESULTS.md §3）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_quantile.run_mix
出力: tools/measure/tr_quantile/results_mix/{rates.csv, verdict.csv, coef.csv}
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tools.measure.tr_quantile import tq as Q
from tools.measure.tr_quantile.run import HERE, MIN_N, PERIODS, ROOT, TFS, TOL

OUT = HERE / "results_mix"


def load(period, tf):
    path, start, end = PERIODS[period]
    d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "high", "low", "close"], parse_dates=["date"]).iloc[:-1]
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5]
    d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
    return Q.features_mix(d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float))[:4]


def main():
    OUT.mkdir(exist_ok=True)
    rows, coefs = [], []
    for tf in TFS:
        yi, vi, ri, xi = load("IS", tf)
        yo, vo, ro, xo = load("OOS", tf)
        b = Q.har_fit(yi, xi)
        fi_raw = b[0] + xi @ b[1:]
        floor = float(fi_raw[fi_raw > 0].min())
        fi, fo = Q.har_predict(b, xi, floor), Q.har_predict(b, xo, floor)
        coefs.append(dict(tf=tf, b0=b[0], b_atr5=b[1], b_atr20=b[2], b_atr100=b[3],
                          neg_is=int((fi_raw <= 0).sum()), neg_oos=int(((b[0] + xo @ b[1:]) <= 0).sum())))
        edges = np.quantile(vi, [0.2, 0.4, 0.6, 0.8])
        edges_f = np.quantile(fi, [0.2, 0.4, 0.6, 0.8])
        bucket = np.digitize(vo, edges)
        for q in Q.QS:
            target = (1 - q) * 100
            for m in Q.METHODS_MIX:
                p = Q.fit(m, yi, vi, ri, q, edges, f=fi, edges_f=edges_f)
                th = Q.threshold(m, p, vo, ro, edges, f=fo, edges_f=edges_f)
                ex = yo > th
                rows.append(dict(tf=tf, q=q, target=target, method=m, group="全体", n=len(yo),
                                 rate=ex.mean() * 100, mean_th=float(np.mean(th))))
                for k in range(5):
                    mk = bucket == k
                    rows.append(dict(tf=tf, q=q, target=target, method=m, group=f"区分{k + 1}", n=int(mk.sum()),
                                     rate=ex[mk].mean() * 100 if mk.any() else np.nan,
                                     mean_th=float(np.mean(th[mk])) if mk.any() else np.nan))
        print(tf, "done", flush=True)
    t = pd.DataFrame(rows)
    t["err"] = t.rate - t.target
    t.to_csv(OUT / "rates.csv", index=False)
    pd.DataFrame(coefs).to_csv(OUT / "coef.csv", index=False)
    v = t[t.n >= MIN_N].groupby(["tf", "q", "method"]).agg(max_abs_err=("err", lambda s: s.abs().max())).reset_index()
    v["pass"] = v.max_abs_err <= TOL
    v.to_csv(OUT / "verdict.csv", index=False)
    pd.set_option("display.width", 220)
    print(pd.DataFrame(coefs).round(3).to_string(index=False))
    print(v.pivot_table(index=["tf", "q"], columns="method", values="max_abs_err").round(2).to_string())
    print("合格", int(v["pass"].sum()), "/", len(v), v[v["pass"]][["tf", "q", "method"]].to_string(index=False))


if __name__ == "__main__":
    main()
