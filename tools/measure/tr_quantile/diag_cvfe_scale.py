"""cvfe の物差しのずれの原因調査（RESULTS.md §7）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_quantile.diag_cvfe_scale
出力: tools/measure/tr_quantile/results_cvfe/diag.csv・diag_vendor.csv
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indigators.cvfe.src.ohlc import compute_cvfe_from_ohlc
from tools.measure.tr_quantile import tq as Q
from tools.measure.tr_quantile.run import HERE, ROOT, TFS

OUT = HERE / "results_cvfe"
SRC = {"duka": "data/marketdata/rollups/jp225_tick_bid/jp225_tick_bid_{tf}.csv",
       "mt5": "data/marketdata/rollups/jp225_mt5/jp225_mt5_{tf}.csv"}
SPLIT = pd.Timestamp("2020-05-01")


def bars(vendor, tf, start):
    d = pd.read_csv(ROOT / SRC[vendor].format(tf=tf), usecols=["date", "open", "high", "low", "close"],
                    parse_dates=["date"]).iloc[:-1]
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5]
    return d[d.date >= start].reset_index(drop=True)


def scale(d, refit_every=0):
    """足ごとの（日時, 物差し = 実際の値幅 ÷ σ̂_{t+1}）と HAR 係数。"""
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = (d.date.astype("int64") // 1_000_000_000).to_numpy(float)
    res = compute_cvfe_from_ohlc(o, h, l, c, t, refit_every=refit_every)
    tr = Q.true_range(h, l, c)
    y = np.concatenate([tr[1:], [np.nan]]) / c * 100
    s = np.concatenate([res.sigma_hat[1:], [np.nan]]) * 100
    ok = np.isfinite(y) & np.isfinite(s) & (s > 0)
    return d.date[ok].reset_index(drop=True), (y[ok] / s[ok]), res.har_coef


def med(dates, r, lo=None, hi=None):
    m = np.ones(len(r), bool)
    if lo is not None:
        m &= (dates >= lo).to_numpy()
    if hi is not None:
        m &= (dates < hi).to_numpy()
    return float(np.median(r[m])), int(m.sum())


def main():
    rows, vend = [], []
    for tf in TFS:
        d14 = bars("duka", tf, "2014-01-01")
        d20 = bars("duka", tf, "2020-05-01")
        m20 = bars("mt5", tf, "2020-05-01")
        a_dates, a_r, a_coef = scale(d14)              # 2014-01 の 500 本で係数
        b_dates, b_r, b_coef = scale(d20)              # 2020-05 の 500 本で係数（Dukascopy）
        c_dates, c_r, c_coef = scale(m20)              # 2020-05 の 500 本で係数（MT5）
        e_dates, e_r, _ = scale(d14, refit_every=500)  # 500 本ごとに決め直す
        rows.append(dict(
            tf=tf,
            x1_duka_fit2014_on2020=med(a_dates, a_r, SPLIT)[0],
            x1_duka_fit2020_on2020=med(b_dates, b_r, SPLIT)[0],
            x2_mt5_fit2020_on2020=med(c_dates, c_r, SPLIT)[0],
            fit2014_on_is=med(a_dates, a_r, None, SPLIT)[0],
            x3_refit_is=med(e_dates, e_r, None, SPLIT)[0], x3_refit_oos=med(e_dates, e_r, SPLIT)[0],
            coef_fit2014=np.round(a_coef, 3).tolist(), coef_duka2020=np.round(b_coef, 3).tolist(),
            coef_mt5_2020=np.round(c_coef, 3).tolist()))
        # 業者の比較: 同じ時刻の足どうしで PARK と実際の値幅の比
        j = d20.merge(m20, on="date", suffixes=("_d", "_m"))
        park = lambda h, l: np.log(h / l) ** 2
        pd_, pm = park(j.high_d, j.low_d), park(j.high_m, j.low_m)
        hl_d, hl_m = (j.high_d - j.low_d) / j.close_d, (j.high_m - j.low_m) / j.close_m
        ok = (pd_ > 0) & (pm > 0)
        vend.append(dict(tf=tf, matched=len(j), park_ratio_mt5_over_duka=float(np.median(pm[ok] / pd_[ok])),
                         hl_ratio_mt5_over_duka=float(np.median(hl_m[ok] / hl_d[ok])),
                         zero_range_duka=float((pd_ == 0).mean() * 100), zero_range_mt5=float((pm == 0).mean() * 100)))
        print(tf, "done", flush=True)
    pd.DataFrame(rows).to_csv(OUT / "diag.csv", index=False)
    pd.DataFrame(vend).to_csv(OUT / "diag_vendor.csv", index=False)
    pd.set_option("display.width", 250)
    print(pd.DataFrame(rows).drop(columns=[c for c in rows[0] if c.startswith("coef")]).round(3).to_string(index=False))
    print(pd.DataFrame(rows)[["tf", "coef_fit2014", "coef_duka2020", "coef_mt5_2020"]].to_string(index=False))
    print(pd.DataFrame(vend).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
