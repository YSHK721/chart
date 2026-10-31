"""真の値幅の β の検定を、事前登録（RESULTS.md §1）どおりに実行する。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_beta.run
出力: tools/measure/tr_beta/results/tests.csv
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.atr_guide.make_page import DUKA, MT5, load, true_range, wilder
from tools.measure.tr_beta import beta as B
from tools.measure.tr_quantile.tq import hour_factor

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
COUNT = 1999
MARGIN = 250
SEED = 20261031
NS = (20, 50, 100)
MIN_GAIN = 0.5  # §1.6 の実用基準（ポイント）
PERIODS = {"IS": (DUKA, "2014-01-01", "2020-05-01"), "OOS": (MT5, "2020-05-01", "2100-01-01")}


def series(path: str, tf: str, start: str, end: str, ns=NS, with_long: bool = False, hours=None) -> dict:
    """期間の終わりまでの足で、評価する足（start 以降）の量を作る。hours を渡すと時間帯の係数にそれを使う。"""
    d = load(path, tf)
    d = d[d.date < end].reset_index(drop=True)
    x = B.range_pct(d, true_range)[1:]
    date = d.date.iloc[1:].reset_index(drop=True)
    first = int((date >= start).to_numpy().argmax())
    factors = None
    if tf == "1h":
        hour = date.dt.hour.to_numpy()
        factors = hour_factor(x[first:], hour[first:]) if hours is None else hours
        x = x / factors[hour]
    out = B.measures(x, first, ns)
    out.update(w=wilder(x, 14)[first:], bars=len(x) - first, hours=factors)
    if with_long:
        out["week"] = pd.factorize(date.iloc[first:].dt.to_period("W"))[0]
        for n in ns:
            lo = max(first - n + 1, 0)
            out[f"l{n}"] = B.past_mean(x[lo:], n)[first - lo:]
    return out


def main():
    data = {(p, tf): series(path, tf, st, en) for p, (path, st, en) in PERIODS.items() for tf in ("1h", "1D")}
    rng = np.random.default_rng(SEED)
    rows = []
    for tf in ("1h", "1D"):
        is_ = data["IS", tf]
        ok_is = np.isfinite(is_["y"]) & np.isfinite(is_["w"])
        a, b = B.fit_level(is_["y"], is_["w"], ok_is)
        for n in NS:
            c = B.fit_sign(B.level_residual(is_["y"], is_["w"], a, b), is_[n], ok_is & (is_[n] != 0))
            for p in PERIODS:
                s = data[p, tf]
                shifts = B.draw_shifts(s["bars"], COUNT, MARGIN, rng)
                up = np.where(np.isfinite(s["y"]) & np.isfinite(s["a"]), (s["y"] > s["a"]).astype(float), np.nan)
                e = B.level_residual(s["y"], s["w"], a, b)
                h, h0, hp = B.circular_test(s[n], up, B.hit_rate, shifts)
                g, g0, gp = B.circular_test(s[n], e, B.mean_gap, shifts)
                ok = np.isfinite(s["y"]) & (s[n] != 0) & np.isfinite(s["w"])
                base = np.exp(a + b * np.log(s["w"]))
                rows.append(dict(period=p, tf=tf, n=n, bars=int(ok.sum()), a=a, b=b, c=c,
                                 H=h, H_null=h0, H_p=hp, D=g, D_null=g0, D_p=gp,
                                 err_base=B.relative_error(s["y"], base, ok),
                                 err_beta=B.relative_error(s["y"], base * np.exp(c * s[n]), ok),
                                 share_up=float(np.nanmean(up))))
    df = pd.DataFrame(rows)
    df = B.judge(B.judge(df, "H"), "D")
    df["err_gain"] = df.err_base - df.err_beta
    df["usable"] = df.D_pass & (df.err_gain >= MIN_GAIN)
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "tests.csv", index=False)
    pd.set_option("display.width", 220, "display.max_columns", 30)
    print(df.round(4).to_string())


if __name__ == "__main__":
    main()
