"""時間帯による値幅の違い（RESULTS.md §11）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_quantile.run_tod
出力: tools/measure/tr_quantile/results_tod/{rates.csv, verdict.csv, hours.csv}
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tools.measure.tr_quantile import tq as Q
from tools.measure.tr_quantile.run import HERE, MIN_N, PERIODS, ROOT, TOL

OUT = HERE / "results_tod"
TFS = ("15m", "1h", "4h")


def load(period, tf):
    """y・v・r・x と、次の足が始まる時刻（日本時間の時）。"""
    path, start, end = PERIODS[period]
    d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "high", "low", "close"], parse_dates=["date"]).iloc[:-1]
    d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
    y, v, r, x, ok = Q.features_mix(d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float))
    nxt = (d.date.shift(-1) + pd.Timedelta(hours=9)).dt.hour.to_numpy()
    return y, v, r, x, nxt[ok].astype(int)


def main():
    OUT.mkdir(exist_ok=True)
    rows, hrs = [], []
    for tf in TFS:
        yi, vi, ri, xi, hi = load("IS", tf)
        yo, vo, ro, xo, ho = load("OOS", tf)
        b = Q.har_fit(yi, xi)
        raw = b[0] + xi @ b[1:]
        floor = float(raw[raw > 0].min())
        fi, fo = Q.har_predict(b, xi, floor), Q.har_predict(b, xo, floor)
        s = Q.hour_factor(yi / fi, hi)
        ti, to = fi * s[hi], fo * s[ho]
        edges = np.quantile(vi, [0.2, 0.4, 0.6, 0.8])
        bucket = np.digitize(vo, edges)
        for h in range(24):
            hrs.append(dict(tf=tf, hour=h, s=s[h], n_is=int((hi == h).sum()), n_oos=int((ho == h).sum()),
                            quiet_next_share=float(((ho == h) & (bucket <= 1)).sum() / max((bucket <= 1).sum(), 1) * 100)))
        variants = {"E": ("E", fi, fo), "F": ("F", fi, fo), "J": ("E", ti, to), "K": ("F", ti, to)}
        for q in Q.QS:
            target = (1 - q) * 100
            for name, (m, f_is, f_oos) in variants.items():
                ef = np.quantile(f_is, [0.2, 0.4, 0.6, 0.8])
                p = Q.fit(m, yi, vi, ri, q, edges, f=f_is, edges_f=ef)
                ex = yo > Q.threshold(m, p, vo, ro, edges, f=f_oos, edges_f=ef)
                rows.append(dict(tf=tf, q=q, target=target, method=name, group="全体", n=len(yo), rate=ex.mean() * 100))
                for k in range(5):
                    mk = bucket == k
                    rows.append(dict(tf=tf, q=q, target=target, method=name, group=f"区分{k + 1}", n=int(mk.sum()),
                                     rate=ex[mk].mean() * 100 if mk.any() else np.nan))
                for h in range(24):
                    mk = ho == h
                    rows.append(dict(tf=tf, q=q, target=target, method=name, group=f"時{h:02d}", n=int(mk.sum()),
                                     rate=ex[mk].mean() * 100 if mk.any() else np.nan))
        print(tf, "done", flush=True)
    t = pd.DataFrame(rows)
    t["err"] = t.rate - t.target
    t.to_csv(OUT / "rates.csv", index=False)
    pd.DataFrame(hrs).to_csv(OUT / "hours.csv", index=False)
    judged = t[(t.n >= MIN_N) & ~t.group.str.startswith("時")]
    v = judged.groupby(["tf", "q", "method"]).agg(max_abs_err=("err", lambda s: s.abs().max())).reset_index()
    hour_err = t[(t.n >= MIN_N) & t.group.str.startswith("時")].groupby(["tf", "q", "method"]).agg(hour_max_err=("err", lambda s: s.abs().max())).reset_index()
    v = v.merge(hour_err, on=["tf", "q", "method"])
    v["pass"] = v.max_abs_err <= TOL
    v.to_csv(OUT / "verdict.csv", index=False)
    pd.set_option("display.width", 220)
    print(v.pivot_table(index=["tf", "q"], columns="method", values=["max_abs_err", "hour_max_err"]).round(1).to_string())
    print("合格", int(v["pass"].sum()), "/", len(v))


if __name__ == "__main__":
    main()
