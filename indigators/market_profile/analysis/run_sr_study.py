"""Step10 CLI — zp 高 z バーの S/R 機能検定（ISSUE-248）。

出力: out/sr_study.json（機械可読）。図は make_sr_figs.py が JSON から描く。

段:
  A  Step9 再現（全セル・方向合算・偽水準 A）
  B  主検定（方向別 × placebo 主対照 / 偽水準 A 副対照・日 FE + 日クラスタ頑健）
  C  z 連続体スキャン（z 帯ごとの日デミーン反応・閾値非依存）
  D  感度（z_thr × k × x × L・主対照のみ）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))

from mp_stats import step10_sr_response as s10  # noqa: E402

_CACHE = _HERE.parents[3] / "data/marketdata/cache/market_profile_zp"
_ZNULL = _CACHE / "znull/JP225/b1/L250-M2000"
_MGRID = _CACHE / "mgrid/JP225"
_OUT = _HERE.parent / "out"

Z_BANDS = [(-99.0, 0.0), (0.0, 1.0), (1.0, 2.0), (2.0, 3.0),
           (3.0, 4.0), (4.0, 6.0), (6.0, 99.0)]


def _d(e: int) -> str:
    return _dt.datetime.fromtimestamp(int(e), _dt.timezone.utc).strftime("%Y-%m-%d")


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="sr_study.json")
    ap.add_argument("--stages", default="ABCD")
    a = ap.parse_args(argv)

    days_all = sorted(int(f[:-4]) for f in os.listdir(_ZNULL) if f.endswith(".npz"))
    if a.max_days:
        days_all = days_all[-a.max_days:]
    t0 = time.time()
    days = s10.load_days(_ZNULL, _MGRID, days_all)
    print(f"usable days: {len(days)}  {_d(days[0].day)} .. {_d(days[-1].day)} "
          f"({time.time()-t0:.1f}s)", flush=True)

    res: dict = {"meta": {
        "symbol": "JP225", "n_days": len(days),
        "period": [_d(days[0].day), _d(days[-1].day)],
        "z_thr": s10.Z_THRESHOLD, "lookback": s10.LOOKBACK_DAYS,
        "k_minutes": s10.REACTION_MINUTES, "x_rows": s10.BOUNCE_ROWS,
        "placebo_rows": s10.PLACEBO_ROWS, "seed": a.seed,
    }}

    # ---- A. Step9 再現 ----
    if "A" in a.stages:
        t = time.time()
        accA = s10.collect(days, groups=("real_cell", "fake_a"), z_thr=s10.Z_THRESHOLD,
                           lookback=s10.LOOKBACK_DAYS, k=s10.REACTION_MINUTES,
                           x=s10.BOUNCE_ROWS)
        r_all = s10.merge(accA[("real_cell", "sup")], accA[("real_cell", "res")])
        f_all = s10.merge(accA[("fake_a", "sup")], accA[("fake_a", "res")])
        res["step9_replication"] = {
            "pooled_real": s10.pooled(r_all),
            "pooled_fake_a": s10.pooled(f_all),
            "paired_equal_weight": s10.paired(r_all, f_all, seed=a.seed),
            "day_fe": s10.paired_fe(r_all, f_all, seed=a.seed),
        }
        print(f"A done ({time.time()-t:.0f}s)",
              json.dumps(res["step9_replication"], ensure_ascii=False, default=float), flush=True)

    # ---- B. 主検定 ----
    if "B" in a.stages:
        t = time.time()
        accB = s10.collect(days, groups=("real_peak", "placebo", "fake_a"),
                           z_thr=s10.Z_THRESHOLD, lookback=s10.LOOKBACK_DAYS,
                           k=s10.REACTION_MINUTES, x=s10.BOUNCE_ROWS)
        main_res: dict = {}
        for s in ("sup", "res"):
            blk = {"pooled_real": s10.pooled(accB[("real_peak", s)]),
                   "pooled_placebo": s10.pooled(accB[("placebo", s)]),
                   "pooled_fake_a": s10.pooled(accB[("fake_a", s)])}
            for metric in ("bounce", "end", "mre", "cont"):
                blk[f"placebo_{metric}"] = s10.paired_fe(
                    accB[("real_peak", s)], accB[("placebo", s)], metric=metric, seed=a.seed)
                blk[f"fake_a_{metric}"] = s10.paired_fe(
                    accB[("real_peak", s)], accB[("fake_a", s)], metric=metric, seed=a.seed)
            main_res[s] = blk
        res["main"] = main_res
        res["profiles"] = {
            f"{g}_{s}": s10.profile_mean(accB[(g, s)]).tolist()
            for s in ("sup", "res") for g in ("real_peak", "placebo", "fake_a")}
        print(f"B done ({time.time()-t:.0f}s)",
              json.dumps(main_res, ensure_ascii=False, default=float)[:2500], flush=True)

    # ---- C. z 連続体スキャン ----
    if "C" in a.stages:
        t = time.time()
        gnames = [f"zb:{lo}:{hi}" for lo, hi in Z_BANDS]
        accC = s10.collect(days, groups=tuple(gnames), z_thr=s10.Z_THRESHOLD,
                           lookback=s10.LOOKBACK_DAYS, k=s10.REACTION_MINUTES,
                           x=s10.BOUNCE_ROWS)
        scan: dict = {}
        for s in ("sup", "res"):
            per = {g: accC[(g, s)] for g in gnames}
            scan[s] = {m: s10.day_mean_by_group(per, metric=m) for m in ("bounce", "end")}
        res["z_scan"] = {"bands": Z_BANDS, "result": scan}
        res["z_profiles"] = {f"{g}_{s}": s10.profile_mean(accC[(g, s)]).tolist()
                             for s in ("sup", "res") for g in gnames}
        print(f"C done ({time.time()-t:.0f}s)",
              json.dumps(scan, ensure_ascii=False, default=float)[:2000], flush=True)

    # ---- D. 感度 ----
    if "D" in a.stages:
        t = time.time()
        grid = []
        combos = [(z, s10.LOOKBACK_DAYS, k, x)
                  for z in (2.0, 3.0, 4.0, 5.0) for k in (10, 30, 60) for x in (2.0, 4.0, 8.0)]
        combos += [(3.0, L, 30, 4.0) for L in (10, 20, 120, 250)]
        for (z, L, k, x) in combos:
            acc = s10.collect(days, groups=("real_peak", "placebo"), z_thr=z,
                              lookback=L, k=k, x=x)
            row = {"z_thr": z, "lookback": L, "k": k, "x": x}
            for s in ("sup", "res"):
                fe = s10.paired_fe(acc[("real_peak", s)], acc[("placebo", s)],
                                   metric="bounce", seed=a.seed, B=1000)
                row[s] = {kk: fe[kk] for kk in ("beta", "se", "t", "p_boot",
                                                "n_days", "n_events_real")}
            grid.append(row)
            print("  D", row["z_thr"], row["lookback"], row["k"], row["x"],
                  f"sup b={row['sup']['beta']:+.4f} t={row['sup']['t']:+.2f}",
                  f"res b={row['res']['beta']:+.4f} t={row['res']['t']:+.2f}", flush=True)
        res["sensitivity"] = grid
        print(f"D done ({time.time()-t:.0f}s)", flush=True)

    with open(_OUT / a.out, "w") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=float)
    print("wrote", _OUT / a.out, flush=True)
    return res


if __name__ == "__main__":
    main()
