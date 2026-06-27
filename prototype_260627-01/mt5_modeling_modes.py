#!/usr/bin/env python3
"""MT5 5モデリングモード × 毎ティック指標再評価 の検証ハーネス（使い捨て試作・A案）。

目的: 売買戦略立案のための検証分析。MT5 の Modelling 5モードごとに、形成中バーの指標
      （EMA）を毎ティック再評価して on_tick シグナルを検出し、モード間でシグナルの件数・
      価格・実行コストがどう変わるかを数値で比較する。ビジュアル要素は持たない。

モード（A案＝下位足M1固定）:
  1. real_ticks  : 実ティック（Parquet・Bid/Ask/mid）。RealTickModel と同一セマンティクス
                   （分グルーピングで高速化＝出力は同じ。RealTickModel の O(bars×ticks) 再走査回避）。
  2. every_tick  : OHLC から多数の疑似ティックを合成（O→H→L→C を線形補間）。MT5 #2 相当。
  3. ohlc_1min   : 既存 OhlcExpandTickModel(order="auto") で O→H→L→C の4疑似ティック。MT5 #3。
  4. open_only   : 既存 OpenOnlyTickModel で始値のみ1ティック。MT5 #4。
  5. math        : ティック非生成。確定バー終値で1回だけ評価（約定なし）。MT5 #5＝検証基準線。

確定足EMAは M1 終値で事前計算し全モード共通（MT5 同様＝指標はバー確定値・モード非依存）。
形成中バーの provisional EMA = ema_prev*(1-α)+price*α を毎ティック評価し、price と EMA水準の
クロスを on_tick シグナルとする。既存 simulator は無改変・データは read-only 参照。
"""
from __future__ import annotations

import glob
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "/workspaces/app-intrabar-tick")
from simulator.domain.bar import Bar  # noqa: E402
from simulator.adapter.execution.tick_model import (  # noqa: E402
    OhlcExpandTickModel,
    OpenOnlyTickModel,
)

TICK_GLOB = "/workspaces/app/data/marketdata/ticks/2018/06/*/JP225_ticks.parquet"
PERIOD = 20
ALPHA = 2.0 / (PERIOD + 1.0)
SYNTH_STEPS = 5   # every_tick 合成: 各レグ(O→H,H→L,L→C)の補間点数


def load_ticks() -> pd.DataFrame:
    files = sorted(glob.glob(TICK_GLOB))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["mid"] = (df["bidPrice"] + df["askPrice"]) / 2.0
    df["minute"] = df["timestamp"].dt.floor("min")
    return df


def build_bars(df: pd.DataFrame):
    """M1 バー（実ティックの mid から OHLC 集計）と、分→実ティック価格列のマップを作る。"""
    g = df.groupby("minute", sort=True)["mid"]
    agg = g.agg(["first", "max", "min", "last", "count"]).reset_index()
    bars: list[Bar] = []
    real_ticks: list[list[float]] = []
    by_min = {k: v.tolist() for k, v in df.groupby("minute", sort=True)["mid"]}
    for row in agg.itertuples(index=False):
        minute, o, h, l, c, cnt = row
        t = np.datetime64(minute.to_datetime64())
        bars.append(Bar(time=t, open=float(o), high=float(h), low=float(l),
                        close=float(c), volume=float(cnt), spread=0))
        real_ticks.append(by_min[minute])
    return bars, real_ticks


def batch_ema(closes: list[float]) -> list[float]:
    ema = [0.0] * len(closes)
    ema[0] = closes[0]
    for i in range(1, len(closes)):
        ema[i] = ema[i - 1] * (1.0 - ALPHA) + closes[i] * ALPHA
    return ema


def synth_every_tick(bar: Bar) -> list[float]:
    """OHLC から多数疑似ティックを線形合成（O→H→L→C・各レグ SYNTH_STEPS 点）。"""
    legs = [(bar.open, bar.high), (bar.high, bar.low), (bar.low, bar.close)]
    out = [bar.open]
    for a, b in legs:
        for k in range(1, SYNTH_STEPS + 1):
            out.append(a + (b - a) * k / SYNTH_STEPS)
    return out


def mode_ticks(mode: str, bar: Bar, prev_close: float, real: list[float],
               ohlc_model: OhlcExpandTickModel, open_model: OpenOnlyTickModel):
    if mode == "real_ticks":
        return real
    if mode == "every_tick":
        return synth_every_tick(bar)
    if mode == "ohlc_1min":
        return [t[0] for t in ohlc_model.ticks_of(bar, prev_close)]
    if mode == "open_only":
        return [t[0] for t in open_model.ticks_of(bar, prev_close)]
    return None  # math は専用処理


def run_mode(mode: str, bars: list[Bar], ema: list[float],
             real_ticks: list[list[float]]):
    """1モードを毎ティック指標再評価で走らせ、シグナル件数・実行時間・約定価格統計を返す。"""
    ohlc_model = OhlcExpandTickModel(order="auto")
    open_model = OpenOnlyTickModel()
    t0 = time.perf_counter()
    signals = 0
    n_ticks = 0
    prev_state = 0
    sig_prices: list[float] = []
    for i in range(1, len(bars)):
        bar = bars[i]
        ema_prev = ema[i - 1]                       # 確定EMA（凍結）= モード非依存
        if mode == "math":
            prices = [bar.close]                    # 終値で1回（約定なし基準）
        else:
            prices = mode_ticks(mode, bar, bars[i - 1].close, real_ticks[i],
                                 ohlc_model, open_model)
        for price in prices:
            n_ticks += 1
            # provisional EMA に対する price のクロス（sign(price-ema_prev) と等価）
            state = 1 if price > ema_prev else (-1 if price < ema_prev else 0)
            if state != 0 and state != prev_state:
                signals += 1
                sig_prices.append(price)
                prev_state = state
            elif state == 0:
                prev_state = 0
    dt = time.perf_counter() - t0
    return {"mode": mode, "ticks": n_ticks, "signals": signals, "secs": dt,
            "sig_prices": sig_prices}


def main() -> None:
    print("== MT5 5モデリングモード × 毎ティック指標再評価 検証 (JP225 2018-06) ==")
    df = load_ticks()
    bars, real_ticks = build_bars(df)
    closes = [b.close for b in bars]
    ema = batch_ema(closes)
    print(f"M1バー: {len(bars):,}  実ティック総数: {len(df):,}  EMA period={PERIOD}\n")

    modes = ["real_ticks", "every_tick", "ohlc_1min", "open_only", "math"]
    results = {}
    for m in modes:
        r = run_mode(m, bars, ema, real_ticks)
        results[m] = r

    base = results["math"]["signals"]   # 基準線＝バー終値のみ評価
    print(f"{'モード':<12}{'評価ティック':>12}{'シグナル':>9}{'vs math':>9}{'時間ms':>9}"
          f"{'シグナル平均価':>12}")
    for m in modes:
        r = results[m]
        avg = (sum(r["sig_prices"]) / len(r["sig_prices"])) if r["sig_prices"] else 0.0
        delta = r["signals"] - base
        print(f"{m:<12}{r['ticks']:>12,}{r['signals']:>9,}{delta:>+9,}"
              f"{r['secs'] * 1000:>9.1f}{avg:>12,.1f}")

    print("\n[読み方] math=バー終値のみ評価の基準線。粒度が細かいモードほど、足内で発生する")
    print("         クロス（math では取りこぼすシグナル）を多く検出する＝戦略検証の解像度が上がる。")


if __name__ == "__main__":
    main()
