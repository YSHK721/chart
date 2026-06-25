"""UC-WV1: 週末バッチ推定（詳細設計 §4.2）。

5分→週次 RS⁺/RS⁻ 集計（純 Python・numpy 不使用）→ VarianceEstimatorPort で予測 →
VarianceForecast 構築 → Repository 保存。日足→週次 GK 実現ボラ（E1 閾値・前週実現）。

usecase 層は domain と自層 Port のみ依存（numpy/pandas を import しない）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from simulator.domain.trading_week import same_trading_day, week_id_of
from simulator.domain.variance_forecast import VarianceForecast
from simulator.usecase.run_weekly_segments import assert_no_lookahead

if TYPE_CHECKING:
    from simulator.domain.bar import Bar
    from simulator.usecase.vol_band_ports import (
        VarianceEstimatorPort,
        VolBandRepositoryPort,
    )

# 隣接 5 分 = 300 秒（欠落・昼休み・オーバーナイト跨ぎ除外）。
_FIVE_MIN_SECONDS = 300


def aggregate_weekly_rs(five_min_bars: "Sequence[Bar]") -> "dict[str, tuple[float, float]]":
    """5分足を週次 RS⁺/RS⁻（符号別二乗和）に集計する純関数（FR-WV-01・D2）。

    同一立会日内の隣接 5 分（=300 秒）のみを用い、log リターン r の符号で RS⁺/RS⁻ に
    振り分ける（r==0 は非寄与）。close<=0 は log(0) 回避でスキップ。
    """
    rs: dict[str, tuple[float, float]] = {}
    bars = list(five_min_bars)
    for prev, cur in zip(bars, bars[1:]):
        if not same_trading_day(prev.time, cur.time):
            continue
        if (int(cur.time) - int(prev.time)) != _FIVE_MIN_SECONDS:
            continue
        if prev.close <= 0 or cur.close <= 0:
            continue
        r = math.log(cur.close / prev.close)
        wid = week_id_of(cur.time)
        rp, rm = rs.get(wid, (0.0, 0.0))
        if r > 0:
            rp += r * r
        elif r < 0:
            rm += r * r
        rs[wid] = (rp, rm)
    return rs


def aggregate_weekly_gk(daily_bars: "Sequence[Bar]") -> "dict[str, float]":
    """日足を週次 GK 実現ボラに集計する純関数（FR-WV-02）。

    GK_d = 0.5*(ln(H/L))² − (2ln2−1)*(ln(C/O))²（負は床 0）。週内 gk_d 合計の平方根。
    """
    week_sum: dict[str, float] = {}
    for d in daily_bars:
        if min(d.open, d.high, d.low, d.close) <= 0:
            continue
        gk_d = (
            0.5 * math.log(d.high / d.low) ** 2
            - (2 * math.log(2) - 1) * math.log(d.close / d.open) ** 2
        )
        gk_d = max(gk_d, 0.0)
        wid = week_id_of(d.time)
        week_sum[wid] = week_sum.get(wid, 0.0) + gk_d
    return {w: math.sqrt(s) for w, s in week_sum.items()}


@dataclass
class EstimateWeeklyBandRequest:
    five_min_bars: "Sequence[Bar]"
    daily_bars: "Sequence[Bar]"
    window: int = 260
    nw_lag: int = 4


@dataclass
class EstimateWeeklyBandResult:
    forecasts: "list[VarianceForecast]"


def _history_week_ids(week_ids: "Sequence[str]", i: int) -> "list[str]":
    """target index i の HAR 入力 history となる week_id 群（厳密に手前のみ）を返す。

    look-ahead 排除（§6・NFR-D4）: target i の予測には week_ids[:i]（当週以降を含まない）
    のみを渡す。本関数の戻りを estimate_weekly_band が assert_no_lookahead で検証する。
    """
    return list(week_ids[:i])


def estimate_weekly_band(
    *,
    request: EstimateWeeklyBandRequest,
    estimator: "VarianceEstimatorPort",
    repo: "VolBandRepositoryPort",
) -> EstimateWeeklyBandResult:
    weekly_rs = aggregate_weekly_rs(request.five_min_bars)
    weekly_gk = aggregate_weekly_gk(request.daily_bars)
    week_ids = sorted(weekly_rs)
    forecasts: list[VarianceForecast] = []
    for i, wid in enumerate(week_ids):
        prev_gk = weekly_gk.get(week_ids[i - 1]) if i >= 1 else None
        hist_ids = _history_week_ids(week_ids, i)
        # look-ahead ガード（§6・NFR-D4）: estimator へ渡す前に当週 wid に対し
        # history week_id 集合が全て {w'<w} であることを本番パスで実検証する。
        assert_no_lookahead(wid, hist_ids)
        hist_plus = [weekly_rs[w][0] for w in hist_ids]
        hist_minus = [weekly_rs[w][1] for w in hist_ids]
        if i < request.window:
            fc = VarianceForecast.no_trade(wid, prev_gk)
        else:
            sp, sm = estimator.forecast(
                hist_plus, hist_minus, window=request.window, nw_lag=request.nw_lag
            )
            if sp is None or sm is None:
                fc = VarianceForecast.no_trade(wid, prev_gk)
            else:
                fc = VarianceForecast(wid, sp, sm, prev_gk, estimable=True)
        forecasts.append(fc)
    repo.save_all(forecasts)
    return EstimateWeeklyBandResult(forecasts)
