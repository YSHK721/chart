"""UC: IS/OOS ウォークフォワード オーケストレーション（詳細設計 v0.1.0 / FR-W1..W9）。

窓を step ずつ前進させながら各窓で SP2 optimize（IS 探索→best→OOS）を反復し、各窓 OOS
を連結（stitch）して通期成績・窓別レポート・WF 効率を出力する。

usecase は domain のみ依存（adapter/framework/main・pandas/numpy(top) を import しない）。
SP2 optimize / optimize_ports と SP1 DegradationReport を部品再利用する（C-W2 無改変）。
時刻は domain と同じく numpy.datetime64 | int を前提にし、窓算術は時刻オブジェクトの
+/<= 演算子のみに依存する（型分岐不要・決定論）。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from simulator.usecase.optimize import (
    OptimizeError,
    OptimizeRequest,
    OptimizeResult,
    optimize,
)
from simulator.usecase.optimize_ports import ObjectivePort, ParameterSearchPort


# --- 例外（無音禁止・FR-W7） -------------------------------------------------

class WalkForwardError(Exception):
    """WF が結果を出せない明示中断（無音禁止・FR-W7）。

    送出条件: 窓 0 件 / 総 run 見積り超過 / 窓内 OptimizeError 昇格 / 空 stitch / mode 不正。
    context（dict）に中断理由の内訳を載せる。
    """

    def __init__(self, message: str, *, context: "dict | None" = None) -> None:
        super().__init__(message)
        self.context = context or {}


# --- dataclass（詳細設計 §3.3） ----------------------------------------------

@dataclass(frozen=True)
class WindowSpec:
    """1 窓の半開区間定義。IS=[is_start, split)、OOS=[split, oos_end)。"""

    index: int
    is_start: Any
    split: Any
    oos_end: Any


@dataclass(frozen=True)
class WalkForwardRequest:
    """WF の入力一式（時刻・span は tools 層で normalize 済）。"""

    mode: str
    global_start: Any
    global_end: Any
    is_span: Any
    oos_span: Any
    step: Any
    search_space: "Mapping[str, list]"
    max_total_runs: int
    metric_names: "tuple[str, ...]" = (
        "profit", "profit_factor", "recovery_factor",
        "expected_payoff", "sharpe_ratio", "trades",
    )
    efficiency_metric: str = "profit"


@dataclass
class WindowResult:
    """窓 i の結果（WindowSpec × SP2 OptimizeResult）。"""

    window: WindowSpec
    best_params: "Mapping[str, Any]"
    is_stats: Any
    oos_stats: Any
    degradation: Any
    optimize_result: "OptimizeResult | None"


@dataclass
class StitchedOosSummary:
    """通期 OOS 連結集約（M-1 3 分類）。"""

    additive: "dict[str, float]"
    recomputed: "dict[str, float | None]"
    per_window: "dict[str, list]"
    window_count: int


@dataclass
class WfEfficiency:
    """WF 効率（profit 固定・None 除外・C-1）。"""

    metric: str
    per_window_ratio: "list[float | None]"
    finite_ratios: "list[float]"
    excluded_none_count: int
    median: "float | None"
    minimum: "float | None"


@dataclass
class WalkForwardResult:
    """WF 全結果（出力）。"""

    windows: "list[WindowSpec]"
    window_results: "list[WindowResult]"
    stitched_oos: StitchedOosSummary
    wf_efficiency: WfEfficiency
    excluded: "dict[str, Any]"


# --- M-1 3 分類フィールド表（models.py:91-143 と 1:1） ----------------------

_ADDITIVE_FIELDS = (
    "profit", "gross_profit", "gross_loss",
    "trades", "profit_trades", "loss_trades",
    "long_trades", "short_trades",
    "profit_long_trades", "profit_short_trades",
)

_NON_STITCHABLE_FIELDS = (
    "initial_deposit", "recovery_factor", "sharpe_ratio", "z_score", "ahpr",
    "balance_min", "balance_dd", "balance_dd_percent",
    "balance_dd_relative", "balance_ddrel_percent", "balance_dd_abs",
    "max_profit_trade", "max_loss_trade",
    "max_con_wins", "max_con_profit_trades", "max_con_losses", "max_con_loss_trades",
    "con_profit_max", "con_profit_max_trades", "con_loss_max", "con_loss_max_trades",
    "profit_trades_avg_con", "loss_trades_avg_con",
    "equity_dd_abs", "equity_dd_max", "equity_dd_max_percent",
)


# --- 純関数（詳細設計 §3.4） -------------------------------------------------

def schedule_windows(
    *,
    mode: str,
    global_start: Any,
    global_end: Any,
    is_span: Any,
    oos_span: Any,
    step: Any,
) -> "list[WindowSpec]":
    """窓列を決定論生成する純関数（FR-W1/W2・H-2/H-3）。

    終了条件 oos_end_i <= global_end（<=）を満たす限り i=0,1,... を採番し、満たさなく
    なった最初の i で単調に打ち切る。窓 0 件は WalkForwardError。
    """
    windows: "list[WindowSpec]" = []
    i = 0
    while True:
        if mode == "rolling":
            is_start = global_start + i * step
            split = is_start + is_span
            oos_end = split + oos_span
        elif mode == "anchored":
            is_start = global_start
            split = global_start + is_span + i * step
            oos_end = split + oos_span
        else:
            raise WalkForwardError(
                "mode must be 'anchored' or 'rolling'", context={"mode": mode}
            )
        if not (oos_end <= global_end):  # H-3 終了条件（単調打ち切り）
            break
        windows.append(WindowSpec(index=i, is_start=is_start, split=split, oos_end=oos_end))
        i += 1
    if not windows:
        raise WalkForwardError(
            "no window satisfies the schedule (global span < is_span + oos_span)",
            context={
                "mode": mode, "global_start": global_start, "global_end": global_end,
                "is_span": is_span, "oos_span": oos_span, "step": step,
            },
        )
    return windows


def stitch_oos(oos_stats_list: "list[Any]") -> StitchedOosSummary:
    """窓別 OOS BacktestStats を M-1 3 分類で連結集約する純関数（FR-W4）。"""
    if not oos_stats_list:
        raise WalkForwardError(
            "stitch_oos received empty oos_stats list", context={"window_count": 0}
        )
    # (A) 加法総和
    additive = {f: 0.0 for f in _ADDITIVE_FIELDS}
    for s in oos_stats_list:
        for f in _ADDITIVE_FIELDS:
            additive[f] += float(getattr(s, f))
    # (B) 母数再計算（A の総和から・窓別比率の平均ではない）
    sgp, sgl = additive["gross_profit"], additive["gross_loss"]
    st, spt, slt = additive["trades"], additive["profit_trades"], additive["loss_trades"]
    recomputed: "dict[str, float | None]" = {
        "profit_factor": (sgp / sgl) if sgl != 0.0 else None,
        "expected_payoff": (additive["profit"] / st) if st != 0.0 else None,
        "average_profit_trade": (sgp / spt) if spt != 0.0 else None,
        "average_loss_trade": (sgl / slt) if slt != 0.0 else None,
    }
    # (C) 連結不能 → 窓別系列のみ（通期スカラ非出力）
    per_window = {
        f: [float(getattr(s, f)) for s in oos_stats_list]
        for f in _NON_STITCHABLE_FIELDS
    }
    return StitchedOosSummary(
        additive=additive, recomputed=recomputed,
        per_window=per_window, window_count=len(oos_stats_list),
    )


def aggregate_efficiency(
    window_results: "list[WindowResult]",
    *,
    metric: str = "profit",
) -> WfEfficiency:
    """窓別 degradation の profit ratio を C-1 規約で集約する純関数（WF-F5・FR-W6）。"""
    per_window_ratio: "list[float | None]" = []
    for wr in window_results:
        md = wr.degradation.by_name(metric)
        per_window_ratio.append(md.ratio if md is not None else None)
    finite = [r for r in per_window_ratio if r is not None]
    excluded = len(per_window_ratio) - len(finite)
    median = statistics.median(finite) if finite else None
    minimum = min(finite) if finite else None
    return WfEfficiency(
        metric=metric, per_window_ratio=per_window_ratio,
        finite_ratios=finite, excluded_none_count=excluded,
        median=median, minimum=minimum,
    )


# --- オーケストレーション本体（詳細設計 §3.4.4） -----------------------------

def walk_forward(
    *,
    request: WalkForwardRequest,
    window_bars_provider: "Callable[[Any, Any], Any]",
    make_run_segment: "Callable[[Mapping[str, Any]], Callable[[Any, Any], Any]]",
    search_port: ParameterSearchPort,
    objective_port: ObjectivePort,
) -> WalkForwardResult:
    """WF オーケストレーション本体（基本設計 §4.3 処理フロー）。

    window_bars_provider(is_start, oos_end) -> 当窓 full（B-1：optimize へ渡す full_bars
    は本コールバックの戻り値のみ）。窓内 OptimizeError は window_index 付きで昇格。
    """
    # 1. 窓スケジュール生成
    windows = schedule_windows(
        mode=request.mode, global_start=request.global_start,
        global_end=request.global_end, is_span=request.is_span,
        oos_span=request.oos_span, step=request.step,
    )
    # 2. 総 run 見積り（H-1 Port 契約）
    per_window_tc = [search_port.theoretical_count(request.search_space) for _ in windows]
    total_run_estimate = sum(per_window_tc) + len(windows)
    if total_run_estimate > request.max_total_runs:
        raise WalkForwardError(
            "total run estimate exceeds max_total_runs",
            context={
                "total_run_estimate": total_run_estimate,
                "max_total_runs": request.max_total_runs,
                "window_count": len(windows),
                "per_window_theoretical_count": per_window_tc,
            },
        )
    # 3. 各窓で optimize（IS 探索 → best → OOS）
    window_results: "list[WindowResult]" = []
    for w in windows:
        bars_i = window_bars_provider(w.is_start, w.oos_end)  # B-1: 当窓 full のみ
        try:
            res_i = optimize(
                request=OptimizeRequest(
                    search_space=request.search_space, split=w.split,
                    is_trading_start=w.is_start, metric_names=request.metric_names,
                ),
                full_bars=bars_i, make_run_segment=make_run_segment,
                search_port=search_port, objective_port=objective_port,
            )
        except OptimizeError as exc:
            raise WalkForwardError(
                f"optimize failed at window index={w.index}",
                context={
                    "window_index": w.index, "optimize_context": exc.context,
                    "optimize_message": str(exc),
                },
            ) from exc
        window_results.append(WindowResult(
            window=w, best_params=res_i.best_params, is_stats=res_i.best_is_stats,
            oos_stats=res_i.oos_stats, degradation=res_i.degradation,
            optimize_result=res_i,
        ))
    # 4. OOS 連結
    stitched = stitch_oos([wr.oos_stats for wr in window_results])
    # 5. WF 効率
    eff = aggregate_efficiency(window_results, metric=request.efficiency_metric)
    # 6. 結果構築
    return WalkForwardResult(
        windows=windows, window_results=window_results, stitched_oos=stitched,
        wf_efficiency=eff,
        excluded={
            "total_run_estimate": total_run_estimate, "window_count": len(windows),
            "per_window_theoretical_count": per_window_tc,
            "efficiency_excluded_none": eff.excluded_none_count,
        },
    )
