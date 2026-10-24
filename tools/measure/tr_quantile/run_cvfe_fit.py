"""cvfe の係数の決め方を 4 案でそろえて G・H を照合し直す（RESULTS.md §9）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_quantile.run_cvfe_fit
出力: tools/measure/tr_quantile/results_cvfe_fit/{rates.csv, verdict.csv, info.csv}
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indigators.cvfe.src.har import har_fit
from tools.measure.tr_quantile import cvfe_fit as CF
from tools.measure.tr_quantile import tq as Q
from tools.measure.tr_quantile.run import HERE, MIN_N, PERIODS, ROOT, TFS, TOL

OUT = HERE / "results_cvfe_fit"
CONDS = ("1 現状", "2 持ち込み", "3 決め直し", "4 ブートストラップ")
BLOCK = {"15m": 92, "1h": 23, "4h": 6, "1D": 5}
N_BOOT = 500
SEED = 20261028


def bars(period, tf):
    path, start, end = PERIODS[period]
    d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "open", "high", "low", "close"],
                    parse_dates=["date"]).iloc[:-1]
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5]
    d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = (d.date.astype("int64") // 1_000_000_000).to_numpy(float)
    return o, h, l, c, t


def sigmas(ohlc, beta_full, s2_full, beta_boot, s2_boot):
    """4 案それぞれの σ̂（足ごと）。"""
    meas, params, q = CF.prepare(*ohlc)
    meas_r, params_r, _ = CF.prepare(*ohlc, refit_every=500)
    return {CONDS[0]: CF.sigma_series(meas, params, q),
            CONDS[1]: CF.sigma_series(meas, params, q, beta_full, s2_full),
            CONDS[2]: CF.sigma_series(meas_r, params_r, q),
            CONDS[3]: CF.sigma_series(meas, params, q, beta_boot, s2_boot)}


def aligned(ohlc, sig):
    """y・v・r と各案の見積もり f（σ̂_{t+1}×100）を、全案で見積もりがそろう足にそろえる。"""
    o, h, l, c, _ = ohlc
    y, v, r, _, ok = Q.features_mix(h, l, c)
    f = {k: (np.concatenate([s[1:], [np.nan]]) * 100)[ok] for k, s in sig.items()}
    keep = np.all([np.isfinite(x) & (x > 0) for x in f.values()], axis=0)
    return y[keep], v[keep], r[keep], {k: x[keep] for k, x in f.items()}


def main():
    OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED)
    rows, info = [], []
    for tf in TFS:
        ois, ooos = bars("IS", tf), bars("OOS", tf)
        meas_is, _, _ = CF.prepare(*ois)
        x, yy = CF.training_rows(meas_is)
        beta_full, s2_full = har_fit(x, yy)
        beta_boot, s2_boot, level = CF.bootstrap_fit(x, yy, BLOCK[tf], N_BOOT, rng)
        _, _, level500 = CF.bootstrap_fit(x[:500], yy[:500], BLOCK[tf], N_BOOT, rng)
        sd_full, sd500 = float(np.std(level)), float(np.std(level500))
        sig_is = sigmas(ois, beta_full, s2_full, beta_boot, s2_boot)
        sig_oos = sigmas(ooos, beta_full, s2_full, beta_boot, s2_boot)
        yi, vi, ri, fi = aligned(ois, sig_is)
        yo, vo, ro, fo = aligned(ooos, sig_oos)
        edges = np.quantile(vi, [0.2, 0.4, 0.6, 0.8])
        bucket = np.digitize(vo, edges)
        for cond in CONDS:
            info.append(dict(tf=tf, cond=cond, ratio_is=float(np.median(yi / fi[cond])),
                             ratio_oos=float(np.median(yo / fo[cond])), n_is=len(yi), n_oos=len(yo),
                             corr_oos=float(np.corrcoef(fo[cond], yo)[0, 1]),
                             level_sd_full=sd_full, level_sd_500=sd500,
                             rows_needed_2pct=float(500 * (sd500 / 2 / 0.02) ** 2),
                             beta_full=np.round(beta_full, 3).tolist(), beta_boot=np.round(beta_boot, 3).tolist()))
            ef = np.quantile(fi[cond], [0.2, 0.4, 0.6, 0.8])
            for q in Q.QS:
                target = (1 - q) * 100
                for name, m in (("G", "E"), ("H", "F")):
                    p = Q.fit(m, yi, vi, ri, q, edges, f=fi[cond], edges_f=ef)
                    th = Q.threshold(m, p, vo, ro, edges, f=fo[cond], edges_f=ef)
                    ex = yo > th
                    rows.append(dict(tf=tf, cond=cond, q=q, target=target, method=name, group="全体", n=len(yo), rate=ex.mean() * 100))
                    for k in range(5):
                        mk = bucket == k
                        rows.append(dict(tf=tf, cond=cond, q=q, target=target, method=name, group=f"区分{k + 1}",
                                         n=int(mk.sum()), rate=ex[mk].mean() * 100 if mk.any() else np.nan))
        print(tf, "done", flush=True)
    t = pd.DataFrame(rows)
    t["err"] = t.rate - t.target
    t.to_csv(OUT / "rates.csv", index=False)
    pd.DataFrame(info).to_csv(OUT / "info.csv", index=False)
    v = t[t.n >= MIN_N].groupby(["tf", "cond", "q", "method"]).agg(max_abs_err=("err", lambda s: s.abs().max())).reset_index()
    v["pass"] = v.max_abs_err <= TOL
    v.to_csv(OUT / "verdict.csv", index=False)
    pd.set_option("display.width", 250)
    print(pd.DataFrame(info)[["tf", "cond", "ratio_is", "ratio_oos", "corr_oos", "level_sd_full", "level_sd_500", "rows_needed_2pct"]].round(3).to_string(index=False))
    print(v.pivot_table(index=["tf", "q"], columns=["method", "cond"], values="max_abs_err").round(1).to_string())
    print("合格", int(v["pass"].sum()), "/", len(v))
    print(v[v["pass"]][["tf", "cond", "q", "method", "max_abs_err"]].to_string(index=False))


if __name__ == "__main__":
    main()
