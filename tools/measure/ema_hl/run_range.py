"""60EMA 安値・高値の線を範囲（±1%・41 本）で、並べ替えた価格の動きと比べる（RESULTS.md §3）。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.ema_hl.run_range
出力: tools/measure/ema_hl/results_range/{profile.csv, tests.csv}
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tools.measure.ema_hl import ema_hl as E
from tools.measure.ema_hl.run import HERE, PERIODS, load

M = 99
SEED = 20261024
OUT = HERE / "results_range"


def profile(bars, in_period, day, n_days, end_minute):
    """{(線, 指標): (平均の配列, 件数の配列)}（41 本ぶん）。"""
    out = {}
    for side in ("lower", "upper"):
        res = E.measure_lines(bars, side, in_period, day, n_days, end_minute, shifts=E.RANGE_SHIFTS)
        for k in E.MEASURES:
            out[(side, k)] = E.totals(res[k])
    return out


def main():
    OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED)
    prof_rows, test_rows = [], []
    for period, (path, start, end) in PERIODS.items():
        dates, bars = load(path)
        in_period = ((dates >= start) & (dates < end)).to_numpy()
        day_all = dates.dt.normalize()
        days = pd.Index(day_all[in_period].unique())
        day = np.zeros(len(dates), dtype=int)
        day[in_period] = days.get_indexer(day_all[in_period])
        n_days = len(days)
        end_minute = float(pd.Timestamp(end).value // 60_000_000_000)
        real = profile(bars, in_period, day, n_days, end_minute)
        # 模擬経路は期間の足だけで作る（線も期間の頭から作り直す）
        pb = {k: v[in_period] for k, v in bars.items()}
        pday = day[in_period]
        nulls = []
        for j in range(M):
            sp = E.shuffled_path(pb, pday, rng)
            nulls.append(profile(sp, np.ones(len(pday), bool), pday, n_days, end_minute))
            if (j + 1) % 20 == 0:
                print(period, "模擬経路", j + 1, flush=True)
        for key in real:
            side, k = key
            rm, rn = real[key]
            nm = np.array([nl[key][0] for nl in nulls])
            nn = np.array([nl[key][1] for nl in nulls])
            null_mean = np.nanmean(nm, axis=0)
            e = rm - null_mean
            z = E.zone_z(e, rn)
            zn = []
            for j in range(M):
                others = np.nanmean(np.delete(nm, j, axis=0), axis=0)
                zn.append(E.zone_z(nm[j] - others, nn[j]))
            zn = np.array(zn)
            p = (1 + (zn >= z).sum()) / (M + 1)
            test_rows.append(dict(period=period, side=side, measure=k, Z=z, Z_null_mean=float(zn.mean()),
                                  Z_null_sd=float(zn.std()), p=p))
            for i, s in enumerate(E.RANGE_SHIFTS):
                prof_rows.append(dict(period=period, side=side, measure=k, shift=s, real=rm[i], n=int(rn[i]),
                                      null_mean=null_mean[i], null_lo=float(np.nanquantile(nm[:, i], 0.025)),
                                      null_hi=float(np.nanquantile(nm[:, i], 0.975)), excess=e[i]))
            print(period, side, k, f"Z={z:+.4f} null={zn.mean():+.4f}±{zn.std():.4f} p={p:.2f}", flush=True)
    t = pd.DataFrame(test_rows)
    is_ = t.period == "IS"
    t.loc[is_, "holm"] = E.holm(t.loc[is_, "p"].to_numpy())
    passed = t[is_ & (t.holm < 0.05)]
    if len(passed):
        sel = (t.period == "OOS") & t.set_index(["side", "measure"]).index.isin(list(zip(passed.side, passed.measure)))
        t.loc[sel, "holm"] = E.holm(t.loc[sel, "p"].to_numpy())
    t.to_csv(OUT / "tests.csv", index=False)
    pd.DataFrame(prof_rows).to_csv(OUT / "profile.csv", index=False)
    pd.set_option("display.width", 200)
    print(t.round(4).to_string(index=False))
    print("IS 通過", len(passed))


if __name__ == "__main__":
    main()
