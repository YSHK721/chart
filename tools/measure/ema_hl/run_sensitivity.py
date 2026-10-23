"""60EMA 安値・高値の線 — パラメータの感度（RESULTS.md §5）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.ema_hl.run_sensitivity
出力: tools/measure/ema_hl/results_sens/sens.csv
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tools.measure.ema_hl import ema_hl as E
from tools.measure.ema_hl.run import HERE, PERIODS, ROOT, load

SPANS = (10, 20, 40, 60, 100, 200)
TFS = {"1m": 1, "5m": 5, "15m": 15}
FAKE_SETS = {"near": (-2, -1, 1, 2), "far": (-6, -3, 3, 6)}
B = 1999
SEED = 20261025
OUT = HERE / "results_sens"
ROLLUP = {"IS": "data/marketdata/rollups/jp225_tick_bid/jp225_tick_bid_{tf}.csv",
          "OOS": "data/marketdata/rollups/jp225_mt5/jp225_mt5_{tf}.csv"}


def source(period, tf):
    return PERIODS[period][0] if tf == "1m" else ROOT / ROLLUP[period].format(tf=tf)


def main():
    OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED)
    rows = []
    for period, (_, start, end) in PERIODS.items():
        for tf, bm in TFS.items():
            dates, bars = load(source(period, tf))
            in_period = ((dates >= start) & (dates < end)).to_numpy()
            day_all = dates.dt.normalize()
            days = pd.Index(day_all[in_period].unique())
            day = np.zeros(len(dates), dtype=int)
            day[in_period] = days.get_indexer(day_all[in_period])
            n_days = len(days)
            end_minute = float(pd.Timestamp(end).value // 60_000_000_000)
            w_boot = rng.multinomial(n_days, np.full(n_days, 1 / n_days), size=B).astype(float)
            for span in SPANS:
                band = (E.ema(bars["high"], span) - E.ema(bars["low"], span)) / bars["close"]
                w = float(np.median(band[in_period]))
                for fset, mults in FAKE_SETS.items():
                    shifts = (0.0,) + tuple(m * w for m in mults)
                    for side in ("lower", "upper"):
                        res = E.measure_lines(bars, side, in_period, day, n_days, end_minute,
                                              shifts=shifts, span=span, bar_minutes=bm)
                        for k in E.MEASURES:
                            d, means = E.d_statistic(res[k])
                            ds = E.bootstrap_d(res[k], w_boot)
                            rows.append(dict(period=period, tf=tf, span=span, fakes=fset, band_w=w, side=side,
                                             measure=k, n=int(res[k][0][1].sum()), real=means[0],
                                             fake=float(np.mean(means[1:])), D=d,
                                             ci_lo=float(np.quantile(ds, .025)), ci_hi=float(np.quantile(ds, .975)),
                                             p_up=(1 + (ds <= 0).sum()) / (B + 1),
                                             p_dn=(1 + (ds >= 0).sum()) / (B + 1)))
                print(period, tf, span, f"w={w*100:.3f}%", flush=True)
    pd.DataFrame(rows).to_csv(OUT / "sens.csv", index=False)


if __name__ == "__main__":
    main()
