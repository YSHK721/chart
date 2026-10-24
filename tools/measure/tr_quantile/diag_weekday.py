"""曜日による値幅の違い（記述のみ・判定なし）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_quantile.diag_weekday
次の足の曜日（日本時間）ごとに、IS・OOS の「y ÷ f」の中央値と、OOS での E・J の超過率（目標 20%）を出す。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tools.measure.tr_quantile import tq as Q
from tools.measure.tr_quantile.run import PERIODS, ROOT

WD = "月火水木金土日"


def load(period, tf):
    path, start, end = PERIODS[period]
    d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "high", "low", "close"], parse_dates=["date"]).iloc[:-1]
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5]
    d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
    y, v, r, x, ok = Q.features_mix(d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float))
    nxt = d.date.shift(-1) + pd.Timedelta(hours=9)
    return y, v, r, x, nxt.dt.hour.to_numpy()[ok].astype(int), nxt.dt.dayofweek.to_numpy()[ok].astype(int)


def main():
    for tf in ("1h", "1D"):
        yi, vi, ri, xi, hi, wi = load("IS", tf)
        yo, vo, ro, xo, ho, wo = load("OOS", tf)
        b = Q.har_fit(yi, xi)
        raw = b[0] + xi @ b[1:]
        floor = float(raw[raw > 0].min())
        fi, fo = Q.har_predict(b, xi, floor), Q.har_predict(b, xo, floor)
        edges = np.quantile(vi, [0.2, 0.4, 0.6, 0.8])
        rows = {}
        variants = {"E": (fi, fo)}
        if tf != "1D":
            s = Q.hour_factor(yi / fi, hi)
            variants["J"] = (fi * s[hi], fo * s[ho])
        ex = {}
        for name, (f_is, f_oos) in variants.items():
            ef = np.quantile(f_is, [0.2, 0.4, 0.6, 0.8])
            p = Q.fit("E", yi, vi, ri, 0.8, edges, f=f_is, edges_f=ef)
            ex[name] = yo > Q.threshold("E", p, vo, ro, edges, f=f_oos, edges_f=ef)
        out = []
        for w in range(7):
            mi, mo = wi == w, wo == w
            if mo.sum() < 30:
                continue
            row = dict(曜日=WD[w], 本数_OOS=int(mo.sum()), 比_IS=float(np.median(yi[mi] / fi[mi])) if mi.any() else np.nan,
                       比_OOS=float(np.median(yo[mo] / fo[mo])))
            for name in ex:
                row[f"超過率_{name}"] = float(ex[name][mo].mean() * 100)
            out.append(row)
        print(tf)
        print(pd.DataFrame(out).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
