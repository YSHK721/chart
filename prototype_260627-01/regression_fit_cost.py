#!/usr/bin/env python3
"""回帰系フィット1回のコスト実測＆ティック粒度への外挿（使い捨て試作）。

問い: 回帰系（全窓フィット）をティック粒度で再計算するのは現実的か。

方針: 実在の回帰系推定器 GkHarEstimator（HARボラ・OLS）の1フィット時間を実測し、
      汎用回帰の floor として numpy OLS窓フィットも測る。これに年間ティック/バー数を掛けて
      「毎ティック再フィット」「毎バー再フィット」「毎日再フィット」の年間コストを比較する。
      tgp(R/MCMC) は本環境に rpy2 が無く実行不可のため、既知の桁（MCMC＝秒オーダー）を併記する。
既存 simulator は無改変。GkHarEstimator は read-only でインポートして呼ぶのみ。
"""
from __future__ import annotations

import sys
import time

import numpy as np

sys.path.insert(0, "/workspaces/app-intrabar-tick/simulator")
from adapter.indicator.gk_har_estimator import GkHarEstimator  # noqa: E402

TICKS_PER_YEAR = 3_606_866   # intrabar_ema_cost.py の実測外挿
BARS_PER_YEAR = 230_217      # 同上（M1）
DAYS_PER_YEAR = 252
N_FITS = 2000                # 計測反復


def measure_gkhar() -> float:
    est = GkHarEstimator()
    rng = np.random.default_rng(0)
    # HAR は日次実現分散系列。窓=60（約3か月）を想定し正の RS 系列を与える。
    rs_plus = np.exp(rng.normal(-9.0, 0.5, 80)).tolist()
    rs_minus = np.exp(rng.normal(-9.0, 0.5, 80)).tolist()
    # ウォームアップ
    for _ in range(50):
        est.forecast(rs_plus, rs_minus, window=60, nw_lag=4)
    t0 = time.perf_counter()
    for _ in range(N_FITS):
        est.forecast(rs_plus, rs_minus, window=60, nw_lag=4)
    return (time.perf_counter() - t0) / N_FITS


def measure_ols_floor(window: int = 200, k: int = 3) -> float:
    """汎用回帰の floor: window点・k特徴の OLS(lstsq) 1回。"""
    rng = np.random.default_rng(1)
    X = rng.normal(size=(window, k))
    y = rng.normal(size=window)
    for _ in range(50):
        np.linalg.lstsq(X, y, rcond=None)
    t0 = time.perf_counter()
    for _ in range(N_FITS):
        np.linalg.lstsq(X, y, rcond=None)
    return (time.perf_counter() - t0) / N_FITS


def fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:8.2f} s"
    if s < 3600:
        return f"{s / 60:8.2f} min"
    if s < 86400:
        return f"{s / 3600:8.2f} h"
    return f"{s / 86400:8.2f} day"


def report(name: str, per_fit: float) -> None:
    print(f"\n== {name}: 1フィット = {per_fit * 1e6:,.1f} µs ==")
    print(f"  毎ティック再フィット (×{TICKS_PER_YEAR:,}/年): {fmt_secs(per_fit * TICKS_PER_YEAR)}")
    print(f"  毎バー  再フィット (×{BARS_PER_YEAR:,}/年): {fmt_secs(per_fit * BARS_PER_YEAR)}")
    print(f"  毎日    再フィット (×{DAYS_PER_YEAR}/年)     : {fmt_secs(per_fit * DAYS_PER_YEAR)}")


def main() -> None:
    print("== 回帰系フィットのティック粒度コスト外挿 (JP225 年換算) ==")
    report("GkHarEstimator (HAR/OLS・実測)", measure_gkhar())
    report("numpy OLS floor (window=200,k=3・実測)", measure_ols_floor())
    # tgp は MCMC（rpy2 不在で実行不可）。既知の桁で外挿のみ。
    for label, per_fit in [("tgp MCMC 楽観 0.1s/fit", 0.1), ("tgp MCMC 現実 1.0s/fit", 1.0)]:
        print(f"\n== {label}（参考・桁外挿） ==")
        print(f"  毎ティック (×{TICKS_PER_YEAR:,}/年): {fmt_secs(per_fit * TICKS_PER_YEAR)}")
        print(f"  毎バー   (×{BARS_PER_YEAR:,}/年): {fmt_secs(per_fit * BARS_PER_YEAR)}")
        print(f"  毎日     (×{DAYS_PER_YEAR}/年)    : {fmt_secs(per_fit * DAYS_PER_YEAR)}")


if __name__ == "__main__":
    main()
