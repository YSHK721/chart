"""足の並びの有意性を、事前登録（RESULTS.md §1）どおりに測る。

実行: lightweight-charts-python-main/.venv/bin/python tools/measure/bar_patterns/run.py
出力: tools/measure/bar_patterns/results/{tests.csv, omnibus.csv}
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import patterns as P  # noqa: E402

TFS = ("15m", "1h", "4h", "1D")
HS = (1, 4)
M = 999
SEED = 20261019
MIN_SHIFT = 500
PERIODS = {
    "IS": (ROOT / "data/marketdata/rollups/jp225_tick_bid/jp225_tick_bid_{tf}.csv", "2014-01-01", "2020-05-01"),
    "OOS": (ROOT / "data/marketdata/rollups/jp225_mt5/jp225_mt5_{tf}.csv", "2020-05-01", None),
}
OUT = HERE / "results"


def load(period: str, tf: str) -> pd.DataFrame:
    path, start, end = PERIODS[period]
    d = pd.read_csv(str(path).format(tf=tf), usecols=["date", "open", "high", "low", "close"], parse_dates=["date"])
    d = d.iloc[:-1]  # 最後の 1 本は形成中の可能性がある
    if tf == "1D":
        d = d[d.date.dt.dayofweek < 5]
    d = d[d.date >= start]
    if end:
        d = d[d.date < end]
    return d.reset_index(drop=True)


def label_sets(d: pd.DataFrame, families):
    o, h, l, c = (d[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    sym_a, sym_b = P.classify(o, c)
    sym_c, sym_d = P.classify_wicks(o, h, l, c)
    syms = {"A": sym_a, "B": sym_b, "C": sym_c, "D": sym_d}
    sets, names = [], []
    for fam in families:
        ks, base, sym = P.FAMILIES[fam], P.BASES[fam], syms[fam]
        for k in ks:
            g = base ** k
            sets.append((P.pattern_labels(sym, base, k), g))
            names += [(fam, k, P.pattern_name(fam, k, code)) for code in range(g)]
    return sets, names


def measure(period: str, tf: str, h: int, rng: np.random.Generator, families):
    d = load(period, tf)
    sets, names = label_sets(d, families)
    fwd = P.forward_returns(d.close.to_numpy(float), h)
    n = len(d)
    shifts = rng.integers(MIN_SHIFT, n - MIN_SHIFT, size=M)
    t_obs, cnt, excess = P.observed(sets, fwd)
    t_null = P.null_tstats(sets, fwd, shifts)
    tested = (cnt >= P.MIN_COUNT) & np.isfinite(t_obs)
    with np.errstate(invalid="ignore"):
        ge = (np.abs(t_null) >= np.abs(t_obs)).sum(axis=0)
        up = (t_null >= t_obs).sum(axis=0)
        dn = (t_null <= t_obs).sum(axis=0)
    df = pd.DataFrame(names, columns=["family", "k", "pattern"])
    df["period"], df["tf"], df["h"] = period, tf, h
    df["n"], df["excess_bp"], df["t"] = cnt, excess * 1e4, t_obs
    df["p2"] = (1 + ge) / (M + 1)
    df["p_up"] = (1 + up) / (M + 1)
    df["p_dn"] = (1 + dn) / (M + 1)
    df["tested"] = tested
    # §1.7: 時間足 × 本数ごとの最大 |t| の帰無（検定対象の並びだけで最大を取る）
    max_null = np.nanmax(np.abs(t_null[:, tested]), axis=1)
    with np.errstate(invalid="ignore"):
        df["p_fw"] = (1 + (max_null[:, None] >= np.abs(t_obs)[None, :]).sum(axis=0)) / (M + 1)
        df["se_bp"] = df.excess_bp / df.t
    # 全体検定: 検定対象の並びの Σt²。帰無は同じ並びの集合で数える（NaN は 0 とみなさず除外）
    s_obs = np.nansum(t_obs[tested] ** 2)
    s_null = np.nansum(t_null[:, tested] ** 2, axis=1)
    omni = dict(period=period, tf=tf, h=h, bars=n, n_tested=int(tested.sum()), S=s_obs,
                S_null_mean=s_null.mean(), p=(1 + (s_null >= s_obs).sum()) / (M + 1),
                start=str(d.date.iloc[0]), end=str(d.date.iloc[-1]),
                mean_close=float(d.close.mean()), t_req95=float(np.quantile(max_null, 0.95)))
    return df, omni


def main():
    # 引数なし = §1 の族 A・B（results/）。"CD" = §3 のひげの族（results_CD/）
    families = tuple(sys.argv[1]) if len(sys.argv) > 1 else ("A", "B")
    out = OUT if families == ("A", "B") else HERE / f"results_{''.join(families)}"
    out.mkdir(exist_ok=True)
    rng = np.random.default_rng(SEED)
    frames, omnis = [], []
    for period in ("IS", "OOS"):
        for tf in TFS:
            for h in HS:
                df, om = measure(period, tf, h, rng, families)
                frames.append(df)
                omnis.append(om)
                print(f"{period} {tf} h={h} bars={om['bars']} tested={om['n_tested']} "
                      f"S={om['S']:.1f} null={om['S_null_mean']:.1f} p={om['p']:.3f}", flush=True)
    tests = pd.concat(frames, ignore_index=True)
    is_ = tests[(tests.period == "IS") & tests.tested].copy()
    is_["q"] = P.bh_qvalues(is_.p2.to_numpy())
    tests = tests.merge(is_[["family", "k", "pattern", "tf", "h", "q"]], how="left",
                        on=["family", "k", "pattern", "tf", "h"])
    tests.loc[tests.period != "IS", "q"] = np.nan
    FW = 0.05 / (len(TFS) * len(HS))
    passed = tests[(tests.period == "IS") & tests.tested & (tests.p_fw <= FW)]
    oos = tests[tests.period == "OOS"].set_index(["family", "k", "pattern", "tf", "h"])
    rows = []
    for _, r in passed.iterrows():
        key = (r.family, r.k, r.pattern, r.tf, r.h)
        o = oos.loc[key]
        p1 = o.p_up if r.t > 0 else o.p_dn
        rows.append(dict(tf=r.tf, h=r.h, pattern=r.pattern, is_n=r.n, is_excess_bp=r.excess_bp,
                         is_t=r.t, is_q=r.q, is_p_fw=r.p_fw, oos_n=o.n, oos_excess_bp=o.excess_bp, oos_t=o.t,
                         oos_p1=p1 if o.tested else np.nan))
    sel = pd.DataFrame(rows)
    if len(sel):
        ok = sel.oos_p1.notna()
        sel.loc[ok, "oos_holm"] = P.holm(sel.loc[ok, "oos_p1"].to_numpy())
    tests.to_csv(out / "tests.csv", index=False)
    pd.DataFrame(omnis).to_csv(out / "omnibus.csv", index=False)
    sel.to_csv(out / "selected.csv", index=False)
    print(f"\nIS 検定数 {len(is_)}・BH 通過 {int((is_.q < 0.05).sum())}・maxT 通過（p_fw ≤ {FW:.5f}）{len(passed)}")
    pd.set_option("display.width", 220)
    if len(sel):
        print(sel.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
