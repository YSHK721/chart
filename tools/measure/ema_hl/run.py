"""1 分足 60EMA 安値・高値の線の反応を、事前登録（RESULTS.md §1）どおりに測る。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.ema_hl.run
出力: tools/measure/ema_hl/results/{tests.csv, means.csv}
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.ema_hl import ema_hl as E

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
B = 1999
SEED = 20261023
PERIODS = {
    "IS": (ROOT / "data/marketdata/jp225_tick_bid_m1.csv", "2014-01-01", "2020-05-01"),
    "OOS": (ROOT / "data/marketdata/jp225_mt5_m1.csv", "2020-05-01", "2100-01-01"),
}
OUT = HERE / "results"


def load(path: Path):
    d = pd.read_csv(path, usecols=["date", "open", "high", "low", "close"], parse_dates=["date"])
    d = d.iloc[:-1]  # 最後の 1 本は形成中の可能性がある
    minutes = (d.date.astype("int64") // 60_000_000_000).to_numpy(float)
    bars = {k: d[k].to_numpy(float) for k in ("open", "high", "low", "close")}
    bars["minutes"] = minutes
    return d.date, bars


def main():
    OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED)
    rows, mean_rows = [], []
    for period, (path, start, end) in PERIODS.items():
        dates, bars = load(path)
        in_period = ((dates >= start) & (dates < end)).to_numpy()
        day_all = dates.dt.normalize()
        days = pd.Index(day_all[in_period].unique())
        day = np.full(len(dates), 0)
        day[in_period] = days.get_indexer(day_all[in_period])
        n_days = len(days)
        end_minute = float(pd.Timestamp(end).value // 60_000_000_000)
        w = rng.multinomial(n_days, np.full(n_days, 1 / n_days), size=B).astype(float)
        for side in ("lower", "upper"):
            res = E.measure_lines(bars, side, in_period, day, n_days, end_minute)
            for k in E.MEASURES:
                d_obs, means = E.d_statistic(res[k])
                d_star = E.bootstrap_d(res[k], w)
                counts = [int(n.sum()) for _, n in res[k]]
                rows.append(dict(period=period, side=side, measure=k, n_real=counts[0],
                                 n_fake_mean=float(np.mean(counts[1:])), real=means[0],
                                 fake=float(np.mean(means[1:])), D=d_obs,
                                 ci_lo=float(np.quantile(d_star, 0.025)), ci_hi=float(np.quantile(d_star, 0.975)),
                                 p_up=(1 + (d_star <= 0).sum()) / (B + 1),
                                 p_dn=(1 + (d_star >= 0).sum()) / (B + 1)))
                for name, m, n in zip(["real"] + [f"fake{s:+.2%}" for s in E.FAKE_SHIFTS], means, counts):
                    mean_rows.append(dict(period=period, side=side, measure=k, line=name, mean=m, n=n))
                print(period, side, k, f"n={counts[0]} real={means[0]:.4f} fake={np.mean(means[1:]):.4f} "
                      f"D={d_obs:+.4f} p_up={rows[-1]['p_up']:.4f}", flush=True)
        mean_rows.append(dict(period=period, side="-", measure="mean_close", line="-",
                              mean=float(bars["close"][in_period].mean()), n=int(in_period.sum())))
    t = pd.DataFrame(rows)
    is_ = t.period == "IS"
    t.loc[is_, "holm"] = E.holm(t.loc[is_, "p_up"].to_numpy())
    passed = t[is_ & (t.holm < 0.05)]
    oos = t[t.period == "OOS"].set_index(["side", "measure"])
    if len(passed):
        keys = list(zip(passed.side, passed.measure))
        p = np.array([oos.loc[k, "p_up"] for k in keys])
        adj = E.holm(p)
        for k, a in zip(keys, adj):
            t.loc[(t.period == "OOS") & (t.side == k[0]) & (t.measure == k[1]), "holm"] = a
    t.to_csv(OUT / "tests.csv", index=False)
    pd.DataFrame(mean_rows).to_csv(OUT / "means.csv", index=False)
    pd.set_option("display.width", 220)
    print(t.round(4).to_string(index=False))
    print(f"IS 通過 {len(passed)}")


if __name__ == "__main__":
    main()
