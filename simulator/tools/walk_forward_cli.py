"""walk_forward_cli.py — IS/OOS ウォークフォワードの実行入口（詳細設計 §4）。

Composition Root。SP1（assert_safe_output_dir/normalize_time/make_run_segment）と
SP2（make_run_segment_factory/GridSearch/RandomSearch/_build_objective_port）を無改変で
再利用し、窓ごとに optimize 機構を回す。

入口検証（factory/optimize 呼出前・無音禁止）:
  - 🟡-2：random で --seed/--n-samples 未指定 → parser.error（exit 2）。
  - B-2：search_space キーが build_interactor 受理キーワードの部分集合でない → parser.error。

pandas・simulator.main の import は tools 層で許容（SP1/SP2 tools と同層）。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from simulator.tools.optimize_cli import (
    _build_objective_port,
    _build_search_port,
    _build_base_kwargs,
    _parse_search_space,
    make_run_segment_factory,
)
from simulator.tools.run_is_oos_cli import assert_safe_output_dir, normalize_time
from simulator.usecase.walk_forward import WalkForwardRequest, walk_forward


# build_interactor（main/__init__.py）の受理キーワード集合（B-2 許容キー）。
_BUILD_INTERACTOR_KEYWORDS = frozenset({
    "data_path", "symbol", "period", "ea_name", "initial_deposit", "contract_size",
    "volume_min", "volume_max", "volume_step", "stops_level", "digits", "point_size",
    "leverage", "ma_period", "ma_method", "lot_size", "stop_loss_points",
    "take_profit_points", "config_overrides", "stop_out_level", "slope_shift",
    "slope_min_points", "entry_offset_points", "entry_type", "trading_start",
    "tick_store_root", "tick_start", "tick_end",
    "weekly_forecast", "weekly_p_tp", "weekly_capital", "weekly_f_risk",
    "adx_min", "adx_period",
    "marketdata_window",
    # E-2（基本設計書 §12.4）で追加された戦略 Decorator の差し込み口。探索対象の
    # スカラーではないが、本集合は build_interactor の実シグネチャと一致させる規約
    # （test_walk_forward_cli.py::test_keyword_whitelist_matches_build_interactor）。
    "strategy_decorator",
    # Phase 6 F-8（依頼者承認済み）で追加された戦略 override の差し込み口。同じく探索
    # 対象スカラーではないが、上記の「実シグネチャと一致させる規約」に従い列挙する。
    "strategy_override",
    # Phase 7（依頼者承認済み）で追加された建玉変更（トレーリング/部分決済）の適用器の
    # 差し込み口。同じく探索対象スカラーではないが、実シグネチャ一致規約に従い列挙する。
    "position_manager",
})


# --- span 正規化（L-1・課題-W3） --------------------------------------------

def _normalize_span(value: str, sample_bar_time: Any) -> Any:
    """span 文字列を時刻型と整合する差分へ正規化（int 秒 / numpy.timedelta64）。"""
    import pandas as pd

    if isinstance(sample_bar_time, int) and not isinstance(sample_bar_time, bool):
        return int(pd.Timedelta(value).total_seconds())
    return pd.Timedelta(value).to_timedelta64()


# --- 出力整形（SP2 to_json_dict/to_markdown 方針踏襲・明示構築） -------------

def _window_dict(wr: Any) -> dict:
    res = wr.optimize_result
    return {
        "index": wr.window.index,
        "is_start": str(wr.window.is_start),
        "split": str(wr.window.split),
        "oos_end": str(wr.window.oos_end),
        "best_params": dict(wr.best_params),
        "is_stats": asdict(wr.is_stats),
        "oos_stats": asdict(wr.oos_stats),
        "degradation": [asdict(m) for m in wr.degradation.metrics],
        "excluded_count": res.excluded_count if res is not None else {},
        "total_candidates": res.total_candidates if res is not None else 0,
        "finite_candidates": res.finite_candidates if res is not None else 0,
    }


def to_json_dict(
    result: Any, request: WalkForwardRequest, objective: str, search_algo: str
) -> dict:
    return {
        "meta": {
            "mode": request.mode,
            "global_start": str(request.global_start),
            "global_end": str(request.global_end),
            "is_span": str(request.is_span),
            "oos_span": str(request.oos_span),
            "step": str(request.step),
            "objective": objective,
            "search_algo": search_algo,
            "window_count": result.excluded["window_count"],
            "total_run_estimate": result.excluded["total_run_estimate"],
            "max_total_runs": request.max_total_runs,
            "efficiency_excluded_none": result.excluded["efficiency_excluded_none"],
        },
        "windows": [_window_dict(wr) for wr in result.window_results],
        "stitched_oos": {
            "window_count": result.stitched_oos.window_count,
            "additive": result.stitched_oos.additive,
            "recomputed": result.stitched_oos.recomputed,
            "per_window": result.stitched_oos.per_window,
        },
        "wf_efficiency": {
            "metric": result.wf_efficiency.metric,
            "per_window_ratio": result.wf_efficiency.per_window_ratio,
            "finite_ratios": result.wf_efficiency.finite_ratios,
            "excluded_none_count": result.wf_efficiency.excluded_none_count,
            "median": result.wf_efficiency.median,
            "minimum": result.wf_efficiency.minimum,
        },
    }


def to_markdown(
    result: Any, request: WalkForwardRequest, objective: str, search_algo: str
) -> str:
    eff = result.wf_efficiency
    lines = [
        "# IS/OOS Walk-Forward Report",
        "",
        f"- mode: {request.mode}",
        f"- global: {request.global_start} .. {request.global_end}",
        f"- is_span / oos_span / step: {request.is_span} / {request.oos_span} / {request.step}",
        f"- objective: {objective}  search_algo: {search_algo}",
        f"- windows: {result.excluded['window_count']}  "
        f"total_run_estimate: {result.excluded['total_run_estimate']}  "
        f"max_total_runs: {request.max_total_runs}",
        "",
        "## Per-Window Summary",
        "| # | IS [start,split) | OOS [split,end) | best_params | IS profit | OOS profit | profit ratio |",
        "|---|---|---|---|---|---|---|",
    ]
    for wr in result.window_results:
        prof = wr.degradation.by_name("profit")
        ratio = "N/A" if prof is None or prof.ratio is None else f"{prof.ratio:.3f}"
        lines.append(
            f"| {wr.window.index} | {wr.window.is_start}..{wr.window.split} | "
            f"{wr.window.split}..{wr.window.oos_end} | {dict(wr.best_params)} | "
            f"{wr.is_stats.profit} | {wr.oos_stats.profit} | {ratio} |"
        )
    lines += ["", "## Stitched OOS (additive totals)", "| metric | total |", "|---|---|"]
    for k, v in result.stitched_oos.additive.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## Stitched OOS (recomputed ratios)", "| metric | value |", "|---|---|"]
    for k, v in result.stitched_oos.recomputed.items():
        lines.append(f"| {k} | {'N/A' if v is None else v} |")
    med = "N/A" if eff.median is None else eff.median
    mn = "N/A" if eff.minimum is None else eff.minimum
    lines += [
        "", "## WF Efficiency (profit ratio)",
        f"- excluded (IS profit=0): {eff.excluded_none_count} window(s)",
        f"- median: {med}   minimum: {mn}",
        "| # | profit ratio |", "|---|---|",
    ]
    for i, r in enumerate(eff.per_window_ratio):
        lines.append(f"| {i} | {'N/A' if r is None else f'{r:.3f}'} |")
    return "\n".join(lines) + "\n"


# --- CLI 引数 ----------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IS/OOS ウォークフォワードの実行入口")
    # base_kwargs（SP2 踏襲）
    p.add_argument("--data-path", required=True)
    p.add_argument("--ea-name", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--symbol", default="JP225")
    p.add_argument("--period", default="M1")
    p.add_argument("--initial-deposit", type=float, default=10_000.0)
    p.add_argument("--contract-size", type=float, default=10.0)
    p.add_argument("--volume-min", type=float, default=0.01)
    p.add_argument("--volume-max", type=float, default=100.0)
    p.add_argument("--volume-step", type=float, default=0.01)
    p.add_argument("--stops-level", type=int, default=0)
    p.add_argument("--digits", type=int, default=1)
    p.add_argument("--point-size", type=float, default=0.1)
    p.add_argument("--leverage", type=float, default=10.0)
    p.add_argument("--ma-period", type=int, default=60)
    p.add_argument("--ma-method", default="ema")
    p.add_argument("--lot-size", type=float, default=0.1)
    p.add_argument("--stop-loss-points", type=int, default=0)
    p.add_argument("--take-profit-points", type=int, default=0)
    p.add_argument("--entry-offset-points", type=float, default=0.0)
    p.add_argument("--entry-type", default="stop")
    p.add_argument("--stop-out-level", type=float, default=100.0)
    p.add_argument("--config-override", action="append", default=[])
    # 探索固有（SP2 踏襲）
    p.add_argument("--search-param", action="append", default=[], required=False)
    # choices は usecase の唯一の登録表から導出する（ISSUE-101 🔵-1・optimize_cli と同源）。
    from simulator.usecase.optimize_strategies import OBJECTIVE_REGISTRY, SEARCH_ALGOS

    p.add_argument("--search-algo", choices=list(SEARCH_ALGOS), required=True)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--n-samples", type=int, default=None)
    p.add_argument("--max-candidates", type=int, required=True)
    p.add_argument("--objective", choices=list(OBJECTIVE_REGISTRY), required=True)
    # WF 固有
    p.add_argument("--mode", choices=["anchored", "rolling"], required=True)
    p.add_argument("--global-start", required=True)
    p.add_argument("--global-end", required=True)
    p.add_argument("--is-span", required=True)
    p.add_argument("--oos-span", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--max-total-runs", type=int, required=True)
    return p


def main(argv: "list[str] | None" = None, *, repo_root: Any = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # --- 入口検証 1：🟡-2（C-2・FR-W8）— _build_search_port 呼出前 ---
    if args.search_algo == "random" and (args.seed is None or args.n_samples is None):
        parser.error(
            "--search-algo random requires both --seed and --n-samples "
            "(omitting either is non-deterministic)"
        )

    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    out_dir = assert_safe_output_dir(args.out_dir, repo_root)

    base_kwargs = _build_base_kwargs(args)
    search_space = _parse_search_space(args.search_param)

    # --- 入口検証 2：B-2 未知探索キー（FR-W9）— factory/optimize 呼出前 ---
    unknown = set(search_space.keys()) - _BUILD_INTERACTOR_KEYWORDS
    if unknown:
        parser.error(
            f"unknown search-param key(s): {sorted(unknown)} "
            "(must be a subset of build_interactor keywords)"
        )

    # --- factory 構築（SP2 再利用・全期間 full_bars 取得・split/is ダミー破棄）---
    factory, full_bars, _split_dummy, _is_start_dummy = make_run_segment_factory(
        base_kwargs, split_str=args.global_start, is_trading_start_str=args.global_start
    )

    sample_time = full_bars[0].time
    global_start = normalize_time(args.global_start, sample_time)
    global_end = normalize_time(args.global_end, sample_time)
    is_span = _normalize_span(args.is_span, sample_time)
    oos_span = _normalize_span(args.oos_span, sample_time)
    step = _normalize_span(args.step, sample_time)

    # --- anchored OOS 接続チェック（課題-W4・無音禁止）---
    if step != oos_span:
        sys.stderr.write(
            f"[warn] step ({args.step}) != oos_span ({args.oos_span}): "
            "OOS windows overlap or gap.\n"
        )

    # --- window_bars_provider（B-1：当窓 full スライス）---
    def window_bars_provider(is_start, oos_end):
        return [b for b in full_bars if is_start <= b.time < oos_end]

    request = WalkForwardRequest(
        mode=args.mode, global_start=global_start, global_end=global_end,
        is_span=is_span, oos_span=oos_span, step=step,
        search_space=search_space, max_total_runs=args.max_total_runs,
    )

    result = walk_forward(
        request=request, window_bars_provider=window_bars_provider,
        make_run_segment=factory, search_port=_build_search_port(args),
        objective_port=_build_objective_port(args),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "walk_forward.json").write_text(
        json.dumps(
            to_json_dict(result, request, args.objective, args.search_algo), indent=2
        ),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(
        to_markdown(result, request, args.objective, args.search_algo), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
