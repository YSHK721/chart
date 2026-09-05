"""段階 2: 刻み × 反転情報量の走査（ISSUE-489・PLAN.md）。読み取り専用。

設計は PLAN.md で走査前に固定済み:
  - 刻み 9 種（5m/15m/30m/1h/2h/4h(b)/267m(c)/8h(b)/1D セッション）
  - 指標 2 種（SMA50 乖離率・EMA50 乖離率）
  - シグナル: 因果ローリング分位 p（窓 500 本・最少 100 本・当該バー除外）の p≤0.05（ロング）／
    p≥0.95（ショート）
  - 評価: シグナル確定時刻（バー終端）から**時計時間** 1h/4h/24h 先の先渡しリターン（bp）。
    イベントは非重複（前イベントのホライズン経過まで採らない）
  - 期間分割固定: IS=〜2020-12-31 / OOS=2021-01-01〜
  - 判定: IS BH(q=0.10) 有意 ∧ OOS 同符号 p<0.05 ∧ 隣接刻み同符号（台地）
"""
from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "marketdata"
BREAK_MINUTES = 100
WINDOW_N = 500
MIN_OBS = 100
MA_LEN = 50
HORIZONS_MIN = (60, 240, 1440)
SPLIT = pd.Timestamp("2021-01-01")

#: 刻み候補（分・"1D" はセッション日）。隣接（台地判定）はこの並び順。
SCALES = [5, 15, 30, 60, 120, 240, 267, 480, "1D"]


def unix_seconds(index: pd.DatetimeIndex) -> np.ndarray:
    return index.values.astype("datetime64[s]").astype("int64")


def broker_day_starts(index: pd.DatetimeIndex) -> np.ndarray:
    local = index.tz_localize("UTC").tz_convert(ZoneInfo("America/New_York"))
    day = (local - pd.Timedelta(hours=18)).normalize()
    start = (day + pd.Timedelta(hours=18)).tz_convert("UTC").tz_localize(None)
    return start.values.astype("datetime64[s]").astype("int64")


def build_bars(m1: pd.DataFrame, scale, day_start: np.ndarray) -> pd.DataFrame:
    """終値バー列（index=バー**終端**の実時刻＝最後の M1 の時刻+60s）。"""
    ts = unix_seconds(m1.index)
    if scale == "1D":
        bar = day_start
    else:
        minutes = int(scale)
        if 60 % minutes == 0 or minutes in (5, 15, 30, 60):
            bar = (ts // (minutes * 60)) * (minutes * 60)     # 1h の約数は床＝起点不変
        else:
            slot = (ts - day_start) // (minutes * 60)
            bar = day_start + slot * minutes * 60
    g = pd.DataFrame({"bar": bar, "close": m1["close"].to_numpy(), "ts": ts})
    agg = g.groupby("bar").agg(close=("close", "last"), end_ts=("ts", "max"))
    agg["end_ts"] = agg["end_ts"] + 60                        # M1 ラベルは分頭 → 確定は +60s
    return agg.reset_index(drop=True)


def causal_rank_last_excl(values: np.ndarray, window: int, min_obs: int) -> np.ndarray:
    """各点 t の「直前 window 本（当該バー除外）の中で v_t 未満の割合」。チャンク化 O(n·w)。"""
    n = values.size
    out = np.full(n, np.nan)
    block = 4000
    for lo in range(1, n, block):
        hi = min(lo + block, n)
        idx = np.arange(lo, hi)
        starts = np.maximum(0, idx - window)
        counts = idx - starts
        ok = counts >= min_obs
        if not ok.any():
            continue
        # 窓行列（block × window）: 端は NaN 詰め
        mat = np.full((hi - lo, window), np.nan)
        for r, i in enumerate(idx):
            mat[r, : i - max(0, i - window)] = values[max(0, i - window): i]
        cur = values[lo:hi][:, None]
        with np.errstate(invalid="ignore"):
            less = np.nansum(mat < cur, axis=1).astype(float)
            valid = np.sum(np.isfinite(mat), axis=1).astype(float)
        rank = np.where((valid >= min_obs) & np.isfinite(values[lo:hi]),
                        less / np.maximum(valid, 1), np.nan)
        out[lo:hi] = rank
    return out


def nonoverlap(times: np.ndarray, horizon_s: int) -> np.ndarray:
    keep = np.zeros(times.size, dtype=bool)
    last = -np.inf
    for i, t in enumerate(times):
        if t >= last + horizon_s:
            keep[i] = True
            last = t
    return keep


def main() -> None:
    m1 = pd.read_csv(DATA / "jp225_tick_m1.csv", parse_dates=["date"]).set_index("date")
    print(f"M1: {m1.index.min()} .. {m1.index.max()}  rows={len(m1)}")
    day_start = broker_day_starts(m1.index)
    m1_ts = unix_seconds(m1.index)
    m1_close = m1["close"].to_numpy()

    def price_at(t: np.ndarray) -> np.ndarray:
        """時計時刻 t の価格（その時点までの最終約定＝pad）。"""
        pos = np.searchsorted(m1_ts, t, side="right") - 1
        pos = np.clip(pos, 0, m1_close.size - 1)
        return m1_close[pos]

    rows = []
    for scale in SCALES:
        bars = build_bars(m1, scale, day_start)
        closes = bars["close"].to_numpy()
        for ind_name, ma in (
            ("sma_dev", pd.Series(closes).rolling(MA_LEN).mean().to_numpy()),
            ("ema_dev", pd.Series(closes).ewm(span=MA_LEN, adjust=False).mean().to_numpy()),
        ):
            with np.errstate(invalid="ignore", divide="ignore"):
                dev = (closes - ma) / ma * 100.0
            p = causal_rank_last_excl(dev, WINDOW_N, MIN_OBS)
            for side, mask in (("long", p <= 0.05), ("short", p >= 0.95)):
                ev_ts = bars["end_ts"].to_numpy()[np.nan_to_num(mask, nan=False).astype(bool)]
                for h in HORIZONS_MIN:
                    keep = nonoverlap(ev_ts, h * 60)
                    t0 = ev_ts[keep]
                    p0, p1 = price_at(t0), price_at(t0 + h * 60)
                    ret = (p1 - p0) / p0 * 1e4          # bp
                    if side == "short":
                        ret = -ret
                    for era, sel in (("IS", t0 < SPLIT.value // 10**9),
                                     ("OOS", t0 >= SPLIT.value // 10**9)):
                        r = ret[sel]
                        if r.size < 30:
                            rows.append((scale, ind_name, side, h, era, r.size,
                                         np.nan, np.nan, np.nan))
                            continue
                        mean = float(r.mean())
                        se = float(r.std(ddof=1) / np.sqrt(r.size))
                        t_stat = mean / se if se > 0 else np.nan
                        # n>=30 の平均検定は正規近似（scipy 非依存・追加ライブラリは承認事項のため使わない）
                        import math
                        pval = float(math.erfc(abs(t_stat) / math.sqrt(2.0)))
                        rows.append((scale, ind_name, side, h, era, int(r.size),
                                     mean, t_stat, pval))
        print(f"  {scale}: bars={len(bars)} 済")

    out = pd.DataFrame(rows, columns=[
        "scale", "indicator", "side", "horizon_min", "era", "n", "mean_bp", "t", "p",
    ])
    out.to_csv(Path(__file__).with_name("scale_scan_results.csv"), index=False)

    # ---- 判定（PLAN.md 固定基準）----
    is_grid = out[(out["era"] == "IS") & out["p"].notna()].copy()
    m = len(is_grid)
    is_grid = is_grid.sort_values("p").reset_index(drop=True)
    is_grid["bh"] = is_grid["p"] <= (np.arange(1, len(is_grid) + 1) / m) * 0.10
    cut = is_grid[is_grid["bh"]].index.max()
    is_grid["bh_pass"] = False
    if pd.notna(cut):
        is_grid.loc[: cut, "bh_pass"] = True
    passed = is_grid[is_grid["bh_pass"] & (is_grid["mean_bp"] > 0)]

    print(f"\n== IS（〜2020-12）BH(q=0.10) 通過: {len(passed)} / {m} セル ==")
    robust = []
    for _, row in passed.iterrows():
        oos = out[(out["era"] == "OOS") & (out["scale"] == row["scale"])
                  & (out["indicator"] == row["indicator"]) & (out["side"] == row["side"])
                  & (out["horizon_min"] == row["horizon_min"])].iloc[0]
        oos_ok = pd.notna(oos["p"]) and oos["mean_bp"] > 0 and oos["p"] < 0.05
        # 台地: 隣接刻みの IS 平均が同符号
        si = SCALES.index(row["scale"])
        neigh = [SCALES[j] for j in (si - 1, si + 1) if 0 <= j < len(SCALES)]
        neigh_means = out[(out["era"] == "IS") & (out["scale"].isin(neigh))
                          & (out["indicator"] == row["indicator"]) & (out["side"] == row["side"])
                          & (out["horizon_min"] == row["horizon_min"])]["mean_bp"]
        plateau = bool((neigh_means > 0).all()) and len(neigh_means) > 0
        tag = "◎頑健" if (oos_ok and plateau) else ("OOS×" if not oos_ok else "台地×")
        robust.append((row, oos, tag))
        print(f"  {row['scale']}|{row['indicator']}|{row['side']}|h={row['horizon_min']}m: "
              f"IS {row['mean_bp']:+.1f}bp (n={row['n']}, p={row['p']:.2e}) / "
              f"OOS {oos['mean_bp']:+.1f}bp (n={oos['n']}, p={oos['p']:.3f}) → {tag}")
    print("\n（詳細 CSV: scale_scan_results.csv）")


if __name__ == "__main__":
    main()
