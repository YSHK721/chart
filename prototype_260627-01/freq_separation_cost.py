#!/usr/bin/env python3
"""頻度分離（低頻度フィット＋毎ティックトリガ）のエンドツーエンド実測（使い捨て試作）。

問い: 「帯=低頻度で再フィット／約定判定=毎ティック」なら、回帰系の帯×ティック・バックテストは
      現実的な時間で完了するか。

方針（核心挙動を忠実に）:
  - 同一の実ティック（JP225 2018-06）を使う。
  - 帯（回帰チャネル）= 1日1回だけ OLS で再フィットし、その日の上下エッジを固定（＝低頻度・重い側）。
    本番では tgp/GkHar に差し替わる。ここは走る numpy OLS で実測し、フィット単価は別測値で年換算する。
  - トリガ = 毎ティック、価格(mid)が当日の帯エッジを上抜け/下抜けしたかを O(1) 判定（＝高頻度・軽い側）。
  - これを pure-Python ループで実測（simulator の実行モデルに合わせる）。
  - 対比として「毎ティック再フィット」した場合の年間コストを、フィット単価×年ティック数で外挿。
既存 simulator は無改変。データは read-only 参照。
"""
from __future__ import annotations

import glob
import time

import numpy as np
import pandas as pd

TICK_GLOB = "/workspaces/app/data/marketdata/ticks/2018/06/*/JP225_ticks.parquet"
FIT_WINDOW = 240          # 帯フィットに使う直近 M1 本数
BAND_K = 2.0              # 帯幅＝残差標準偏差×K
TICKS_PER_YEAR = 3_606_866
DAYS_PER_YEAR = 252
# 別スクリプト regression_fit_cost.py の実測/桁:
PER_FIT = {"OLS floor(20µs)": 20e-6, "GkHar(607µs)": 607e-6, "tgp MCMC(~1s)": 1.0}


def load() -> pd.DataFrame:
    files = sorted(glob.glob(TICK_GLOB))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["mid"] = (df["bidPrice"] + df["askPrice"]) / 2.0
    df["minute"] = df["timestamp"].dt.floor("min")
    df["day"] = df["timestamp"].dt.floor("D")
    return df, len(files)


def m1_bars(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("minute", sort=True)
    bars = g["mid"].last().reset_index(name="close")
    bars["day"] = bars["minute"].dt.floor("D")
    return bars


def fit_band(closes: np.ndarray) -> tuple[float, float]:
    """直近窓の OLS 回帰チャネル → 末端のセンター値と帯幅(σ×K)を返す（帯エッジ算出用）。"""
    n = len(closes)
    x = np.arange(n, dtype=float)
    X = np.column_stack([np.ones(n), x])
    beta = np.linalg.solve(X.T @ X, X.T @ closes)   # OLS
    fitted = X @ beta
    resid_sd = float(np.std(closes - fitted))
    center = float(fitted[-1])                       # 末端のセンター（当日の基準線）
    return center, BAND_K * resid_sd


def run_freq_separation(df: pd.DataFrame, bars: pd.DataFrame) -> dict:
    """日次フィット＋毎ティックトリガをエンドツーエンドで実測。"""
    closes = bars["close"].to_numpy(dtype=float)
    bar_days = bars["day"].to_numpy()
    mids = df["mid"].to_numpy(dtype=float)
    tick_days = df["day"].to_numpy()

    t0 = time.perf_counter()
    # --- 低頻度: 各日の開始時点で直近 FIT_WINDOW 本を OLS フィットし当日の帯エッジを確定 ---
    day_to_band: dict = {}
    unique_days = list(dict.fromkeys(bar_days.tolist()))
    n_fits = 0
    for d in unique_days:
        idx = np.searchsorted(bar_days, d)           # その日の最初のバー位置
        lo = max(0, idx - FIT_WINDOW)
        if idx - lo < 8:                              # 窓が小さすぎる初日はスキップ
            continue
        center, half = fit_band(closes[lo:idx])
        day_to_band[d] = (center + half, center - half)   # (upper, lower)
        n_fits += 1
    fit_secs = time.perf_counter() - t0

    # --- 高頻度: 毎ティック、当日の固定エッジに対しクロス判定（O(1)） ---
    t1 = time.perf_counter()
    signals = 0
    prev_state = 0
    cur_day = None
    upper = lower = None
    for k in range(len(mids)):
        d = tick_days[k]
        if d != cur_day:
            cur_day = d
            band = day_to_band.get(d)
            upper, lower = (band if band else (None, None))
        if upper is None:
            continue
        mid = mids[k]
        state = 1 if mid > upper else (-1 if mid < lower else 0)   # 帯に対する位置
        if state != 0 and state != prev_state:                    # エッジ抜け＝トリガ
            signals += 1
            prev_state = state
        elif state == 0:
            prev_state = 0
    trig_secs = time.perf_counter() - t1

    return {"n_fits": n_fits, "fit_secs": fit_secs, "trig_secs": trig_secs,
            "signals": signals, "n_ticks": len(mids)}


def fmt(s: float) -> str:
    if s < 60:
        return f"{s:6.2f} s"
    if s < 3600:
        return f"{s / 60:6.2f} min"
    if s < 86400:
        return f"{s / 3600:6.2f} h"
    return f"{s / 86400:6.2f} day"


def main() -> None:
    print("== 頻度分離 バックテスト コスト実測 (JP225 2018-06 実ティック) ==")
    df, n_days = load()
    bars = m1_bars(df)
    r = run_freq_separation(df, bars)
    print(f"営業日:{n_days}  M1バー:{len(bars):,}  ティック:{r['n_ticks']:,}  "
          f"日次フィット回数:{r['n_fits']}")
    print(f"窓={FIT_WINDOW}本  帯=±{BAND_K}σ\n")
    print(f"[実測] 日次OLSフィット計 : {r['fit_secs'] * 1000:7.2f} ms")
    print(f"[実測] 毎ティックトリガ  : {r['trig_secs'] * 1000:7.2f} ms  "
          f"signals={r['signals']:,}  ({r['n_ticks'] / r['trig_secs']:,.0f} tick/s)")
    total = r["fit_secs"] + r["trig_secs"]
    print(f"[実測] 月合計           : {total * 1000:7.2f} ms")

    scale = DAYS_PER_YEAR / n_days
    print("\n-- 年換算 (252営業日) --")
    print(f"トリガ(毎ティック)      ≈ {fmt(r['trig_secs'] * scale)}")
    print("帯フィット(日次)＝フィット単価ごと:")
    for name, pf in PER_FIT.items():
        fit_year = pf * DAYS_PER_YEAR        # 日次＝252回/年
        tickrefit_year = pf * TICKS_PER_YEAR  # 毎ティック再フィット（対比）
        total_year = fit_year + r["trig_secs"] * scale
        print(f"  {name:16s}: 頻度分離合計 ≈ {fmt(total_year)}   "
              f"(毎ティック再フィットなら {fmt(tickrefit_year)})")


if __name__ == "__main__":
    main()
