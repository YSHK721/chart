"""実ティック滞在秒との照合 — 分単位滞在（Step5 原子）の妥当性検証。

直近 n_days 営業日について、実ティック滞在秒（tick 間隔を当該 mid の行に積算）で
日次 POC 行を計算し、分単位滞在（ffill 分グリッド）の POC 行との一致を測る。
一致率が高ければ「分単位滞在は実ティック滞在秒の妥当な代理」と結論できる
（Step1-3 後の合意: 最終原子は実ティック滞在秒に帰結。本照合はその橋渡し）。

依存: market_profile_api.compute.market_profile_dwell._load_window_ticks
（read-only 再利用・外れ値除去つき正準ティック窓ローダ）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from . import _REPO_ROOT
from .data_prep import (
    DailyFeatures,
    SessionData,
    ffill_close_grid,
    SESSION_OPEN_MOD,
    SESSION_CLOSE_MOD,
)
from .step5_null_b import N_ROWS, observed_row_counts

_API_DIR = _REPO_ROOT / "indigators/market_profile/api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from market_profile_api.compute.market_profile_dwell import (  # noqa: E402
    _load_window_ticks,
)


def _tick_row_counts(
    secs: "np.ndarray", mids: "np.ndarray", session_end: int, low: float, high: float
) -> "np.ndarray | None":
    """ティック滞在秒の行積算（(N_ROWS,)）。ティック無しは None。

    tick i の滞在秒 = secs[i+1] − secs[i]（最終 tick はセッション終端まで）を
    mid_i の行へ積算する。行グリッドは分単位滞在と同一（[low,high] を N_ROWS 等分）。
    """
    if secs.size == 0:
        return None
    span = high - low
    row_w = span / N_ROWS if span > 0 else 1.0
    dwell = np.empty(secs.size, dtype=float)
    dwell[:-1] = np.diff(secs)
    dwell[-1] = max(0, session_end - int(secs[-1]))
    idx = np.clip(np.floor((mids - low) / row_w).astype(np.int64), 0, N_ROWS - 1)
    return np.bincount(idx, weights=dwell, minlength=N_ROWS).astype(float)


def _poc_row(counts: "np.ndarray", low: float, high: float) -> int:
    """argmax 行（タイは日中間値に近い行・主変種と同一規約）。"""
    span = high - low
    row_w = span / N_ROWS if span > 0 else 1.0
    centers = low + (np.arange(N_ROWS) + 0.5) * row_w
    mx = counts.max()
    cand = np.flatnonzero(counts == mx)
    mid = (high + low) / 2.0
    return int(cand[np.argmin(np.abs(centers[cand] - mid))])


def tick_dwell_agreement(
    sd: SessionData,
    f: DailyFeatures,
    *,
    n_days: int = 250,
    symbol: str = "JP225",
) -> "dict[str, object]":
    """直近 n_days の「ティック滞在秒 POC 行 vs 分単位滞在 POC 行」一致統計を返す。"""
    grid = ffill_close_grid(sd)
    D = sd.n_days
    take = np.arange(max(0, D - n_days), D)
    dist = []
    for d in take:
        day0 = int(sd.day_epoch[d])
        t0 = day0 + SESSION_OPEN_MOD * 60
        t1 = day0 + (SESSION_CLOSE_MOD + 1) * 60
        secs, mids = _load_window_ticks(symbol, t0, t1)
        low, high = float(f.day_low[d]), float(f.day_high[d])
        if high <= low:
            continue
        tick_counts = _tick_row_counts(secs, mids, t1, low, high)
        if tick_counts is None or tick_counts.sum() <= 0:
            continue
        minute_counts = observed_row_counts(grid[d], low, high)
        dist.append(abs(_poc_row(tick_counts, low, high) - _poc_row(minute_counts, low, high)))
    dist = np.asarray(dist, dtype=float)
    if dist.size == 0:
        return {"n_days_compared": 0}
    return {
        "n_days_compared": int(dist.size),
        "agree_exact_rate": float(np.mean(dist == 0)),
        "agree_within_1_row_rate": float(np.mean(dist <= 1)),
        "row_dist_median": float(np.median(dist)),
        "row_dist_p95": float(np.percentile(dist, 95)),
    }
