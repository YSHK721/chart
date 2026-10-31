"""損切りの距離の比較を、事前登録（RESULTS.md §1）どおりに実行する。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.stop_scale.run
出力: tools/measure/stop_scale/results/{compare.csv, levels.csv, tiers.csv, post.csv}
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tools.measure.atr_guide.make_page import DUKA, MT5, load, sma, true_range, wilder
from tools.measure.stop_scale import stop as S

OUT = Path(__file__).resolve().parent / "results"
COUNT = 1999
SEED = 20261031
SIGMAS = (20, 50, 100)
BASE = "W14"
HOLDS = {"1h": (8, 24), "1D": (1, 5)}
SURVIVALS = (0.75, 0.9, 0.95)
PERIODS = {"IS": (DUKA, "2014-01-01", "2020-05-01"), "OOS": (MT5, "2020-05-01", "2100-01-01")}


def frame(path: str, tf: str, start: str, end: str) -> dict:
    """期間の終わりまでの足で、物差しを作る。助走は窓に要る分だけ計算する。"""
    d = load(path, tf)
    d = d[d.date < end].reset_index(drop=True)
    close = d.close.to_numpy()
    first = int((d.date >= start).to_numpy().argmax())
    tr = true_range(d)
    scales = {"W14": wilder(tr, 14) / close * 100, "S20": sma(tr, 20) / close * 100}
    for n in SIGMAS:
        scales[f"s{n}"] = S.residual_sigma(close, n, first) / close * 100
    return dict(high=d.high.to_numpy(), low=d.low.to_numpy(), close=close, first=first, scales=scales,
                block=pd.factorize(d.date.dt.to_period("Q"))[0])


def combo(data: dict, tf: str, t: int, rng) -> tuple[list, list, list]:
    """時間足と持つ本数の 1 組の、比較・倍数と掛かる割合・荒れ方の段の行を返す。倍数は IS で決める。"""
    ev = {p: S.common_events(S.adverse_moves(data[p, tf]["high"], data[p, tf]["low"], data[p, tf]["close"], t),
                             data[p, tf]["scales"], data[p, tf]["block"], data[p, tf]["first"]) for p in PERIODS}
    ks = {name: {sv: S.multiple_for(ev["IS"]["ratio"][name], sv) for sv in SURVIVALS} for name in ev["IS"]["ratio"]}
    edges = np.quantile(ev["IS"]["scale"][BASE], [0.2, 0.4, 0.6, 0.8])
    names = [f"s{n}" for n in SIGMAS]
    rows, levels, tiers = [], [], []
    for p in PERIODS:
        e = ev[p]
        draws = S.picks(int(e["block"].max()) + 1, COUNT, rng)
        for name in e["ratio"]:
            for sv in SURVIVALS:
                levels.append(dict(period=p, tf=tf, t=t, scale=name, survival=sv, k=ks[name][sv],
                                   hit=S.hit_rate(e["ratio"][name], ks[name][sv]),
                                   distance=S.distance(e["ratio"][name], e["scale"][name], sv),
                                   gap=S.rate_gap(e["ratio"][name], e["block"], ks[name][sv], (1 - sv) * 100)))
            rates = S.tier_rates(e["ratio"][name], e["scale"][BASE], edges, ks[name][0.9])
            tiers.append(dict(period=p, tf=tf, t=t, scale=name, **{f"tier{i + 1}": v for i, v in enumerate(rates)}))
        table = S.distance_table(e, [BASE, *names], draws)
        for name in names:
            c1 = S.compare_distance(table, name, BASE)
            c2 = S.compare_gap(e, name, BASE, ks[name][0.9], ks[BASE][0.9], draws)
            rows.append(dict(period=p, key=f"{tf}-{t}-{name}", tf=tf, t=t, scale=name, events=len(e["block"]),
                             blocks=int(e["block"].max()) + 1,
                             **{f"distance_{k}": v for k, v in c1.items()}, **{f"gap_{k}": v for k, v in c2.items()}))
    return rows, levels, tiers


def post_rows(data: dict, tf: str, t: int) -> list:
    """§3 の事後の記述（OOS）: 固定 % の対照と、距離の要約を替えた比。"""
    d = data["OOS", tf]
    scales = {**d["scales"], "fixed": np.ones(len(d["close"]))}
    e = S.common_events(S.adverse_moves(d["high"], d["low"], d["close"], t), scales, d["block"], d["first"])
    i = data["IS", tf]
    ei = S.common_events(S.adverse_moves(i["high"], i["low"], i["close"], t), {**i["scales"], "fixed": np.ones(len(i["close"]))}, i["block"], i["first"])
    rows = []
    for name in e["ratio"]:
        rows.append(dict(tf=tf, t=t, scale=name, mean=S.distance(e["ratio"][name], e["scale"][name]),
                         geometric=S.summary_distance(e["ratio"][name], e["scale"][name], S.geometric),
                         harmonic=S.summary_distance(e["ratio"][name], e["scale"][name], S.harmonic),
                         gap=S.rate_gap(e["ratio"][name], e["block"], S.multiple_for(ei["ratio"][name], 0.9)),
                         q90_q50=S.multiple_for(e["ratio"][name], 0.9) / S.multiple_for(e["ratio"][name], 0.5)))
    return rows


def main():
    data = {(p, tf): frame(path, tf, st, en) for p, (path, st, en) in PERIODS.items() for tf in HOLDS}
    rng = np.random.default_rng(SEED)
    rows, levels, tiers, post = [], [], [], []
    for tf, holds in HOLDS.items():
        for t in holds:
            r, l, ti = combo(data, tf, t, rng)
            rows += r
            levels += l
            tiers += ti
            post += post_rows(data, tf, t)
    df = S.judge(S.judge(pd.DataFrame(rows), "distance"), "gap")
    df["verdict"] = [S.verdict(r) if r.period == "OOS" else "" for r in df.itertuples()]
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "compare.csv", index=False)
    pd.DataFrame(levels).to_csv(OUT / "levels.csv", index=False)
    pd.DataFrame(tiers).to_csv(OUT / "tiers.csv", index=False)
    pd.DataFrame(post).to_csv(OUT / "post.csv", index=False)
    pd.set_option("display.width", 240, "display.max_columns", 40)
    print(df.verdict[df.period == "OOS"].value_counts().to_string())
    print(pd.DataFrame(tiers).round(1).to_string())
    print(pd.DataFrame(post).round(3).to_string())


if __name__ == "__main__":
    main()
