"""見積もりを cvfe（チャートと同じ OHLC 経路）に替えた G・H を、A〜F と同じ足にそろえて測る（RESULTS.md §5）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_quantile.run_cvfe
出力: tools/measure/tr_quantile/results_cvfe/{rates.csv, verdict.csv, info.csv}
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indigators.cvfe.src.ohlc import compute_cvfe_from_ohlc
from tools.measure.tr_quantile import tq as Q
from tools.measure.tr_quantile.run import HERE, MIN_N, PERIODS, ROOT, TFS, TOL

OUT = HERE / "results_cvfe"
#: 出力の名前 → (tq の決め方, 見積もり)。G・H は E・F の見積もりを cvfe に替えたもの
METHODS = {"A": ("A", None), "B": ("B", None), "C": ("C", None), "D": ("D", None),
           "E": ("E", "har"), "F": ("F", "har"), "G": ("E", "cvfe"), "H": ("F", "cvfe")}


def load(period, tf):
    """期間の足で、y・v・r・3 つの ATR%・cvfe の見積もり（σ̂_{t+1}×100）がそろう足だけ返す。"""
    path, start, end = PERIODS[period]
    d = pd.read_csv(ROOT / path.format(tf=tf), usecols=["date", "open", "high", "low", "close"],
                    parse_dates=["date"]).iloc[:-1]
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5]
    d = d[(d.date >= start) & (d.date < end)].reset_index(drop=True)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = (d.date.astype("int64") // 1_000_000_000).to_numpy(float)
    res = compute_cvfe_from_ohlc(o, h, l, c, times)
    sig_next = np.concatenate([res.sigma_hat[1:], [np.nan]]) * 100  # 足 t の起点で見る次の足の σ̂
    ok_next = np.concatenate([res.available[1:], [False]])
    y, v, r, x, ok = Q.features_mix(h, l, c)
    fc = sig_next[ok]
    keep = np.isfinite(fc) & (fc > 0) & ok_next[ok]
    return y[keep], v[keep], r[keep], x[keep], fc[keep], res.measure_id, res.quality_gate


def main():
    OUT.mkdir(exist_ok=True)
    rows, info = [], []
    for tf in TFS:
        yi, vi, ri, xi, ci, mid, gate = load("IS", tf)
        yo, vo, ro, xo, co, _, _ = load("OOS", tf)
        b = Q.har_fit(yi, xi)
        raw = b[0] + xi @ b[1:]
        floor = float(raw[raw > 0].min())
        hi_, ho = Q.har_predict(b, xi, floor), Q.har_predict(b, xo, floor)
        corr = lambda f, y: float(np.corrcoef(f, y)[0, 1])
        info.append(dict(tf=tf, measure_id=mid, quality_gate=gate, n_is=len(yi), n_oos=len(yo),
                         corr_har_oos=corr(ho, yo), corr_cvfe_oos=corr(co, yo),
                         ratio_cvfe_is=float(np.median(yi / ci)), ratio_cvfe_oos=float(np.median(yo / co)),
                         ratio_atr_is=float(np.median(yi / vi)), ratio_atr_oos=float(np.median(yo / vo))))
        edges = np.quantile(vi, [0.2, 0.4, 0.6, 0.8])
        bucket = np.digitize(vo, edges)
        est = {"har": (hi_, ho), "cvfe": (ci, co)}
        for q in Q.QS:
            target = (1 - q) * 100
            for name, (m, src) in METHODS.items():
                fi, fo = est[src] if src else (None, None)
                ef = np.quantile(fi, [0.2, 0.4, 0.6, 0.8]) if src else None
                p = Q.fit(m, yi, vi, ri, q, edges, f=fi, edges_f=ef)
                th = Q.threshold(m, p, vo, ro, edges, f=fo, edges_f=ef)
                ex = yo > th
                rows.append(dict(tf=tf, q=q, target=target, method=name, group="全体", n=len(yo), rate=ex.mean() * 100))
                for k in range(5):
                    mk = bucket == k
                    rows.append(dict(tf=tf, q=q, target=target, method=name, group=f"区分{k + 1}", n=int(mk.sum()),
                                     rate=ex[mk].mean() * 100 if mk.any() else np.nan))
        print(tf, "done", flush=True)
    t = pd.DataFrame(rows)
    t["err"] = t.rate - t.target
    t.to_csv(OUT / "rates.csv", index=False)
    pd.DataFrame(info).to_csv(OUT / "info.csv", index=False)
    v = t[t.n >= MIN_N].groupby(["tf", "q", "method"]).agg(max_abs_err=("err", lambda s: s.abs().max())).reset_index()
    v["pass"] = v.max_abs_err <= TOL
    v.to_csv(OUT / "verdict.csv", index=False)
    pd.set_option("display.width", 220)
    print(pd.DataFrame(info).round(3).to_string(index=False))
    print(v.pivot_table(index=["tf", "q"], columns="method", values="max_abs_err").round(2).to_string())
    print("合格", int(v["pass"].sum()), "/", len(v), v[v["pass"]][["tf", "q", "method"]].to_string(index=False))


if __name__ == "__main__":
    main()
