"""UC-WV3: 検証手続き S1〜S6（詳細設計 §4.4・仕様 §3.2）。

S1 IS/OOS 週数ベース分割（floor 0.7）→ S2 全20候補 f_k → S3 SPA p → S4 判定 →
S5 OOS 選択候補1回 → S6 Kupiec/Christoffersen 採否。決定論。

usecase 層は domain と自層 Port のみ依存（numpy/pandas を import しない）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Sequence

from simulator.domain.backtest_test_result import BacktestTestResult, Verdict
from simulator.domain.trading_week import week_id_of
from simulator.usecase.run_weekly_segments import WeeklySegmentOutcome

if TYPE_CHECKING:
    from simulator.domain.bar import Bar
    from simulator.usecase.validation_ports import BacktestTestPort, SpaTestPort


CandidateRunner = Callable[["Sequence[Bar]", str, float], "list[WeeklySegmentOutcome]"]


@dataclass
class ValidateStrategyRequest:
    full_bars: "Sequence[Bar]"
    capital: float
    f_risk: float = 0.01
    alpha_stop: float = 0.05
    seed: int = 0
    B: int = 5000
    min_weeks: int = 260
    min_stop_hits: int = 30
    e_grid: "tuple[str, ...]" = ("E0", "E1(0.5)", "E1(1.0)", "E1(1.5)", "E1(2.0)")
    p_tp_grid: "tuple[float, ...]" = (0.40, 0.50, 0.60, 0.70)


def unique_week_ids(bars: "Sequence[Bar]") -> "list[str]":
    """bars に出現する week_id を昇順・重複なしで返す。"""
    return sorted({week_id_of(b.time) for b in bars})


def _argmax(values: "Sequence[float]") -> int:
    best_i = 0
    best_v = values[0]
    for i, v in enumerate(values):
        if v > best_v:
            best_v = v
            best_i = i
    return best_i


def _insufficient(spa_p, best_f_k, oos_weeks, oos_stop_hits) -> BacktestTestResult:
    return BacktestTestResult(
        Verdict.INSUFFICIENT_SAMPLE, spa_p, None, None, best_f_k,
        None, None, None, None, oos_weeks, oos_stop_hits,
    )


def validate_strategy(
    *,
    request: ValidateStrategyRequest,
    run_candidate: CandidateRunner,
    spa: "SpaTestPort",
    tests: "BacktestTestPort",
) -> BacktestTestResult:
    # S1 IS/OOS 分割（floor 0.7）
    weeks = unique_week_ids(request.full_bars)
    W = len(weeks)
    split_idx = math.floor(W * 0.7)
    is_week_ids = set(weeks[:split_idx])
    oos_week_ids = set(weeks[split_idx:])
    is_bars = [b for b in request.full_bars if week_id_of(b.time) in is_week_ids]
    oos_bars = [b for b in request.full_bars if week_id_of(b.time) in oos_week_ids]

    # S2 IS 全候補 f_k・週×候補行列
    candidates = [(e, p) for e in request.e_grid for p in request.p_tp_grid]
    per_cand_weekly: dict[tuple, list[float]] = {}
    f_k: list[float] = []
    for (e, p) in candidates:
        outs = run_candidate(is_bars, e, p)
        weekly_ret = [o.log.net_pnl / request.capital for o in outs]
        per_cand_weekly[(e, p)] = weekly_ret
        f_k.append(sum(weekly_ret) / len(weekly_ret) if weekly_ret else 0.0)
    n_is = len(per_cand_weekly[candidates[0]])
    f_matrix = [[per_cand_weekly[c][w] for c in candidates] for w in range(n_is)]

    # サンプル下限（IS 側・第3結果）
    if n_is < 1 or W < request.min_weeks:
        return _insufficient(
            None, max(f_k) if f_k else None, len(oos_week_ids), 0
        )

    # S3 SPA
    spa_p = spa.spa_pvalue(f_matrix, seed=request.seed, B=request.B)

    # S4 判定
    best_idx = _argmax(f_k)
    best = f_k[best_idx]
    if spa_p >= 0.05 or not (best > 0.0):
        return BacktestTestResult(
            Verdict.REJECT_STRATEGY, spa_p, None, None, best,
            None, None, None, None, len(oos_week_ids), 0,
        )
    e_star, p_star = candidates[best_idx]

    # S5 OOS（選択候補のみ1回）
    oos_outs = run_candidate(oos_bars, e_star, p_star)
    oos_weekly_ret = [o.log.net_pnl / request.capital for o in oos_outs]
    oos_mean = sum(oos_weekly_ret) / len(oos_weekly_ret) if oos_weekly_ret else 0.0
    hit_series = [1 if o.log.exit_type == "stop" else 0 for o in oos_outs if o.log.entry_flag]
    tp_hits = sum(1 for o in oos_outs if o.log.exit_type == "tp" and o.log.entry_flag)
    traded = sum(1 for o in oos_outs if o.log.entry_flag)
    stop_hits = sum(hit_series)

    # サンプル下限（OOS 側）
    if len(oos_week_ids) < request.min_weeks or stop_hits < request.min_stop_hits:
        return _insufficient(spa_p, best, len(oos_week_ids), stop_hits)

    # S6 検定
    kupiec_p = tests.kupiec(hit_series, alpha=request.alpha_stop)
    chris_p = tests.christoffersen_independence(hit_series)
    tp_rate = (tp_hits / traded) if traded else 0.0
    tp_calib = abs(tp_rate - p_star)

    cond_a = oos_mean > 0.0
    cond_b = kupiec_p >= 0.05
    cond_c = chris_p >= 0.05
    verdict = Verdict.ADOPT if (cond_a and cond_b and cond_c) else Verdict.NOT_ADOPT
    return BacktestTestResult(
        verdict, spa_p, e_star, p_star, best,
        kupiec_p, chris_p, tp_calib, oos_mean,
        len(oos_week_ids), stop_hits,
    )
