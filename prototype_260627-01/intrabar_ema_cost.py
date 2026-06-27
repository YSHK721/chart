#!/usr/bin/env python3
"""イントラバー・ティックEMA の実行コスト実測（使い捨て試作）。

問い: 実ティックで「形成中バーの増分EMA＋on_tick 評価」をティック粒度で回した実行時間は、
      1分足粒度に対しどれだけ増えるか。ティック検証は現実的な時間で完了するか。

方針（核心挙動を忠実に・simulator のループ実行モデルに合わせて pure-Python ループで計測）:
  - 同一の実ティック（JP225 2018-06）から M1 バーを集計し、両経路を同条件で比較する。
  - bar 経路 : EMA を M1 終値でバッチ計算（現 simulator と同じ）→ M1 ごとに pure-Python で
               クロス判定（on_new_bar 相当）。
  - tick 経路: 確定バー EMA を凍結し、形成中バーの EMA を毎ティック O(1) で増分更新
               （ema_prev*(1-α)+mid*α）→ 毎ティック クロス判定（on_tick 相当）。
  既存 simulator は一切改変しない。データは read-only で参照する。
"""
from __future__ import annotations

import glob
import time

import pandas as pd

TICK_GLOB = "/workspaces/app/data/marketdata/ticks/2018/06/*/JP225_ticks.parquet"
PERIOD = 20
ALPHA = 2.0 / (PERIOD + 1.0)
YEAR_DAYS = 252  # 年換算用の営業日


def load_ticks() -> pd.DataFrame:
    files = sorted(glob.glob(TICK_GLOB))
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["mid"] = (df["bidPrice"] + df["askPrice"]) / 2.0
    df["minute"] = df["timestamp"].dt.floor("min")
    return df, len(files)


def aggregate_m1(df: pd.DataFrame) -> pd.DataFrame:
    """M1 バー（終値＝その分の最終 mid）。"""
    return df.groupby("minute", sort=True)["mid"].last().reset_index(name="close")


def batch_ema(closes: list[float]) -> list[float]:
    """確定足 EMA のバッチ計算（現 simulator 相当）。"""
    ema = [0.0] * len(closes)
    ema[0] = closes[0]
    for i in range(1, len(closes)):
        ema[i] = ema[i - 1] * (1.0 - ALPHA) + closes[i] * ALPHA
    return ema


def run_bar_path(closes: list[float]) -> tuple[float, int]:
    """bar 経路: バッチ EMA ＋ M1 ごとの pure-Python クロス判定。"""
    t0 = time.perf_counter()
    ema = batch_ema(closes)
    signals = 0
    prev_diff = closes[0] - ema[0]
    for i in range(1, len(closes)):
        diff = closes[i] - ema[i]
        if (prev_diff <= 0.0 < diff) or (prev_diff >= 0.0 > diff):
            signals += 1
        prev_diff = diff
    return time.perf_counter() - t0, signals


def run_tick_path(mids: list[float], minute_keys: list[int]) -> tuple[float, int]:
    """tick 経路: 形成中バー EMA を毎ティック O(1) 増分更新＋毎ティック クロス判定。

    確定バー EMA(ema_prev) を凍結し、形成中バーの provisional EMA を
        prov = ema_prev*(1-α) + mid*α
    で毎ティック算出。分境界が変わったら直前 provisional を確定 EMA として ema_prev に繰り上げる。
    """
    t0 = time.perf_counter()
    signals = 0
    ema_prev = mids[0]      # 直前確定バーの EMA（シード=最初の mid）
    prov = mids[0]
    cur_minute = minute_keys[0]
    prev_diff = 0.0
    for k in range(len(mids)):
        m = minute_keys[k]
        if m != cur_minute:           # 分境界: 直前 provisional を確定として繰り上げ
            ema_prev = prov
            cur_minute = m
        mid = mids[k]
        prov = ema_prev * (1.0 - ALPHA) + mid * ALPHA   # O(1) 増分更新
        diff = mid - prov
        if (prev_diff <= 0.0 < diff) or (prev_diff >= 0.0 > diff):   # on_tick クロス判定
            signals += 1
        prev_diff = diff
    return time.perf_counter() - t0, signals


def main() -> None:
    print("== イントラバー・ティックEMA コスト実測 (JP225 2018-06) ==")
    df, n_days = load_ticks()
    n_ticks = len(df)
    bars = aggregate_m1(df)
    closes = bars["close"].astype(float).tolist()
    n_bars = len(closes)
    mids = df["mid"].astype(float).tolist()
    # 分キーを整数化（比較高速化）。
    minute_keys = df["minute"].astype("int64").tolist()

    print(f"営業日: {n_days}  M1バー: {n_bars:,}  ティック: {n_ticks:,}  "
          f"(平均 {n_ticks / n_bars:.1f} tick/bar, {n_ticks / n_days:,.0f} tick/day)")
    print(f"EMA period={PERIOD} (α={ALPHA:.4f})\n")

    # 数回計測して中央値的に最小値を採る（GC/ウォームアップ揺れ除去）。
    bar_t = min(run_bar_path(closes)[0] for _ in range(3))
    _, bar_sig = run_bar_path(closes)
    tick_t = min(run_tick_path(mids, minute_keys)[0] for _ in range(3))
    _, tick_sig = run_tick_path(mids, minute_keys)

    print(f"[bar 経路 ] {bar_t * 1000:8.2f} ms  signals={bar_sig:,}  "
          f"({n_bars / bar_t:,.0f} bar/s)")
    print(f"[tick経路 ] {tick_t * 1000:8.2f} ms  signals={tick_sig:,}  "
          f"({n_ticks / tick_t:,.0f} tick/s)")
    print(f"\n増加率(tick/bar 時間): {tick_t / bar_t:.1f}x")
    print(f"ティック1本あたり: {tick_t / n_ticks * 1e6:.3f} µs/tick")

    # 年換算（O(1)/tick なので線形外挿）。
    ticks_year = n_ticks / n_days * YEAR_DAYS
    bars_year = n_bars / n_days * YEAR_DAYS
    print("\n-- 年換算 (252営業日・線形外挿) --")
    print(f"ティック/年: {ticks_year:,.0f}  → tick経路 ≈ {tick_t / n_ticks * ticks_year:6.2f} s")
    print(f"M1バー/年 : {bars_year:,.0f}  → bar 経路 ≈ {bar_t / n_bars * bars_year:6.2f} s")


if __name__ == "__main__":
    main()
