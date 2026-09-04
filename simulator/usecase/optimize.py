"""UC: IS/OOS 最適化オーケストレーション（基本設計 v0.2.0 / FR-O1..O9）。

探索空間×目的関数で IS を探索し best params を確定、best params を凍結して OOS で
検証し劣化レポートを返す。エンジン実行手段は make_run_segment ファクトリ（params->
run_segment）として tools 層から注入する（DIP・課題-O1）。

usecase は domain のみ依存（adapter/framework/main・pandas を import しない）。
SP1 run_is_oos の純関数（slice_is_bars/build_degradation_report）と DegradationReport を
部品再利用するが、run_is_oos 関数は呼ばない（IS/OOS 対称契約に非対称ループが不適合・課題-O2）。
SP1 は無改変（C2）。時刻は domain と同じく numpy.datetime64 | int を前提にする。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from simulator.usecase.optimize_ports import ObjectivePort, ParameterSearchPort, ParamSet
from simulator.usecase.run_is_oos import (
    DegradationReport,
    RunSegment,
    build_degradation_report,
    slice_is_bars,
)

# params -> その params の 1 区間実行 run_segment を返すファクトリ（課題-O1）。
MakeRunSegment = Callable[[ParamSet], RunSegment]


class OptimizeError(Exception):
    """最適化が結果を出せない場合の明示中断（無音禁止・C-1/M-1/M-2）。

    送出条件:
      (a) 理論候補数 > max_candidates（ParameterSearchPort.candidates 内・M-2）
      (b) 有効候補 0 件（全候補が非有限スコアまたは失敗・C-1/M-1）
      (c) 事前検証 NG（IS/OOS 空区間・範囲不正）

    context（dict）に中断理由の内訳を載せる。
    """

    def __init__(self, message: str, *, context: "dict | None" = None) -> None:
        super().__init__(message)
        self.context = context or {}


@dataclass(frozen=True)
class OptimizeRequest:
    """最適化の入力（基本設計 §5.2・§6.1）。"""

    search_space: "Mapping[str, list]"
    split: Any
    is_trading_start: Any
    metric_names: "tuple[str, ...]" = (
        "profit",
        "profit_factor",
        "recovery_factor",
        "expected_payoff",
        "sharpe_ratio",
        "trades",
    )


@dataclass
class TrialRecord:
    """探索 1 試行の記録（基本設計 §5.2・High-2/C-1/M-1）。"""

    params: "ParamSet"
    is_score: "float | None"
    is_finite: bool
    failed: bool
    failure_reason: "str | None"
    is_stats: Any
    is_best: bool = False


@dataclass
class OptimizeResult:
    """最適化の出力一式（基本設計 §5.2・§6.1）。"""

    best_params: "ParamSet"
    best_is_stats: Any
    best_is_score: float
    oos_stats: Any
    degradation: DegradationReport
    trials: "list[TrialRecord]"
    excluded_count: "dict[str, int]"
    total_candidates: int
    finite_candidates: int


def optimize(
    *,
    request: OptimizeRequest,
    full_bars: Any,
    make_run_segment: "MakeRunSegment",
    search_port: ParameterSearchPort,
    objective_port: ObjectivePort,
) -> OptimizeResult:
    """IS 探索 -> best 確定 -> OOS 検証を 1 回行う（FR-O1..O9）。

    例外: OptimizeError（上限超過 / 有効候補 0 件 / 事前検証 NG）。
          候補ごとの ConfigError/BacktestError/MarginCallError は捕捉・除外・継続（M-1）。
    """
    from simulator.domain.exceptions import (  # usecase->domain（内向き・許容）
        BacktestError,
        ConfigError,
        MarginCallError,
    )

    full_list = list(full_bars)
    is_bars = slice_is_bars(full_list, request.split)

    # (1) 空区間検証（探索ループ前に 1 回・split/is_trading_start は全候補共通）
    oos_count = sum(1 for b in full_list if b.time >= request.split)
    if len(is_bars) < 1:
        raise OptimizeError(
            "IS 区間が空（bar.time < split を満たすバーが 0 件）",
            context={"phase": "pre_validation"},
        )
    if oos_count < 1:
        raise OptimizeError(
            "OOS 区間が空（bar.time >= split を満たすバーが 0 件）",
            context={"phase": "pre_validation"},
        )
    if not (request.is_trading_start <= request.split):
        raise OptimizeError(
            "is_trading_start は split 以下である必要がある",
            context={"phase": "pre_validation"},
        )

    # (2)(3) 候補列挙 + IS run ループ（上限超過は candidates が OptimizeError を送出）
    trials: "list[TrialRecord]" = []
    for params in search_port.candidates(request.search_space):
        params = dict(params)  # 防御的コピー
        rs = make_run_segment(params)
        try:
            is_stats = rs(is_bars, request.is_trading_start)
        except (ConfigError, BacktestError, MarginCallError) as exc:  # M-1
            trials.append(
                TrialRecord(
                    params=params,
                    is_score=None,
                    is_finite=False,
                    failed=True,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                    is_stats=None,
                )
            )
            continue
        score = objective_port.score(is_stats)
        is_finite = math.isfinite(score)  # C-1
        trials.append(
            TrialRecord(
                params=params,
                is_score=score,
                is_finite=is_finite,
                failed=False,
                failure_reason=None,
                is_stats=is_stats,  # High-2: 保持
            )
        )

    total = len(trials)
    excluded_failed = sum(1 for t in trials if t.failed)
    excluded_nonfinite = sum(1 for t in trials if (not t.failed) and (not t.is_finite))

    # (4) 有限・非失敗の母集合で argmax（tie=列挙順先勝ち）
    finite = [t for t in trials if t.is_finite and not t.failed]
    if not finite:
        raise OptimizeError(
            "有効候補 0 件（全候補が非有限スコアまたは失敗）",
            context={
                "total_candidates": total,
                "excluded_nonfinite": excluded_nonfinite,
                "excluded_failed": excluded_failed,
                "finite_candidates": 0,
            },
        )
    best = finite[0]
    for t in finite[1:]:
        if t.is_score > best.is_score:  # 厳密 > のみ更新 = tie は先出（列挙順先勝ち）
            best = t
    best.is_best = True
    best_is_stats = best.is_stats  # High-2: 保持値を採用（再 run しない）

    # (5) OOS run（best params で別 build 1 回 = N_cand+1 番目の run）
    rs_best = make_run_segment(best.params)
    oos_stats = rs_best(full_list, request.split)  # OOS: full + trading_start=split

    # (6) 劣化算出（SP1 再利用）
    degradation = build_degradation_report(
        best_is_stats, oos_stats, request.metric_names
    )

    # (7) 結果構築
    return OptimizeResult(
        best_params=best.params,
        best_is_stats=best_is_stats,
        best_is_score=best.is_score,
        oos_stats=oos_stats,
        degradation=degradation,
        trials=trials,
        excluded_count={"nonfinite": excluded_nonfinite, "failed": excluded_failed},
        total_candidates=total,
        finite_candidates=len(finite),
    )
