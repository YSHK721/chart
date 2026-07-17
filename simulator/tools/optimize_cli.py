"""optimize_cli.py — IS/OOS 最適化の実行入口（詳細設計 §2.4）。

責務＝結線（Composition Root 利用側・committed/SP1 無改変＝C2/C3）:
  1. CLI 引数解釈（argparse）。探索空間・探索アルゴリズム・目的関数・max_candidates 等。
  2. base build_interactor(**base_kwargs) で full_bars と時刻正規化サンプルを得る。
  3. make_run_segment_factory（params->run_segment ファクトリ・課題-O1）を構成。
  4. search_port/objective_port を選択・注入。
  5. optimize(...) を呼ぶ。
  6. assert_safe_output_dir（SP1 再利用）-> JSON/Markdown 整形 -> 新規 OUT 書込。

pandas・simulator.main の import は tools 層では許容（Composition Root 利用側・SP1 と同層）。
SP1 run_is_oos_cli の make_run_segment/normalize_time/assert_safe_output_dir を再利用（無改変）。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping

from simulator.tools.run_is_oos_cli import assert_safe_output_dir
from simulator.usecase.optimize import OptimizeRequest, optimize


def make_run_segment_factory(
    base_kwargs: dict,
    *,
    split_str: str,
    is_trading_start_str: str,
) -> "tuple[Callable[[Mapping], Callable[[Any, Any], Any]], Any, Any, Any]":
    """base_kwargs を閉包し params -> run_segment を返すファクトリを構成（課題-O1）。

    戻り値:
      factory          : params -> run_segment（UC へ注入）
      full_bars        : base build の request.bars（IS slice の入力）
      split            : normalize_time(split_str, sample_time)
      is_trading_start : normalize_time(is_trading_start_str, sample_time)
    """
    from simulator.main import build_interactor
    from simulator.tools.run_is_oos_cli import make_run_segment, normalize_time

    # base build（full_bars と時刻正規化サンプルの取得・1 回・execute なし）。
    base_controller, base_request = build_interactor(**base_kwargs)
    full_bars = base_request.bars
    sample_time = full_bars[0].time
    split = normalize_time(split_str, sample_time)
    is_trading_start = normalize_time(is_trading_start_str, sample_time)

    def factory(params: "Mapping[str, Any]") -> "Callable[[Any, Any], Any]":
        controller, request = build_interactor(**{**base_kwargs, **params})  # 候補ごと再構築
        return make_run_segment(controller, request)  # SP1 閉包（execute 直叩き・B-1）

    return factory, full_bars, split, is_trading_start


# --- Port 実装の選択・注入 ----------------------------------------------------

def _build_search_port(args) -> Any:
    from simulator.usecase.optimize_strategies import GridSearch, RandomSearch

    if args.search_algo == "grid":
        return GridSearch(max_candidates=args.max_candidates)
    return RandomSearch(
        seed=args.seed, n_samples=args.n_samples, max_candidates=args.max_candidates
    )


def _build_objective_port(args) -> Any:
    # 目的関数の唯一の登録表（usecase.OBJECTIVE_REGISTRY・ISSUE-101 🔵-1）から生成する。
    from simulator.usecase.optimize_strategies import OBJECTIVE_REGISTRY

    return OBJECTIVE_REGISTRY[args.objective]()


# --- 出力整形（SP1 L-1 方針継承・新規 presenter なし） -------------------------

def to_json_dict(result: Any) -> dict:
    """OptimizeResult を JSON シリアライズ可能 dict へ（asdict パターン・SP1 踏襲）。"""
    return {
        "best_params": dict(result.best_params),
        "best_is_stats": asdict(result.best_is_stats),
        "best_is_score": result.best_is_score,
        "oos_stats": asdict(result.oos_stats),
        "degradation": [asdict(m) for m in result.degradation.metrics],
        "trials": [
            {
                "params": dict(t.params),
                "is_score": t.is_score,
                "is_finite": t.is_finite,
                "failed": t.failed,
                "failure_reason": t.failure_reason,
                "is_best": t.is_best,
            }
            for t in result.trials  # is_stats は best_is_stats に集約・trials では除外
        ],
        "excluded_count": result.excluded_count,
        "total_candidates": result.total_candidates,
        "finite_candidates": result.finite_candidates,
    }


def to_markdown(
    result: Any, *, split: Any = None, is_trading_start: Any = None, objective: Any = None
) -> str:
    """best params 表＋IS best｜OOS｜劣化の並列表＋探索ログ表（人間可読・SP1 形式踏襲）。"""
    total = result.total_candidates
    finite = result.finite_candidates
    nf = result.excluded_count.get("nonfinite", 0)
    fa = result.excluded_count.get("failed", 0)
    lines = [
        "# IS/OOS Optimization Report",
        "",
        f"- split: {split}",
        f"- is_trading_start: {is_trading_start}",
        f"- objective: {objective}",
        f"- candidates: total={total} finite={finite} excluded(nonfinite={nf} failed={fa})",
        "",
        "## Best Parameters",
        "| param | value |",
        "|---|---|",
    ]
    for k, v in dict(result.best_params).items():
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## IS(best) vs OOS Degradation",
        "| metric | IS(best) | OOS | ratio (OOS/IS) | delta (OOS-IS) |",
        "|---|---|---|---|---|",
    ]
    for m in result.degradation.metrics:
        ratio = "N/A" if m.ratio is None else f"{m.ratio:.3f}"
        lines.append(f"| {m.name} | {m.is_value} | {m.oos_value} | {ratio} | {m.delta} |")
    lines += [
        "",
        "## Trial Log",
        "| # | params | is_score | finite | failed | best |",
        "|---|---|---|---|---|---|",
    ]
    for i, t in enumerate(result.trials):
        best_mark = "*" if t.is_best else ""
        finite_mark = "yes" if t.is_finite else "no"
        failed_mark = "yes" if t.failed else "no"
        lines.append(
            f"| {i} | {dict(t.params)} | {t.is_score} | {finite_mark} | {failed_mark} | {best_mark} |"
        )
    return "\n".join(lines) + "\n"


# --- CLI 引数 ----------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IS/OOS 最適化の実行入口")
    # base_kwargs（SP1 _build_arg_parser 踏襲）
    p.add_argument("--data-path", required=True)
    p.add_argument("--ea-name", required=True)
    p.add_argument("--split", required=True)
    p.add_argument("--is-trading-start", required=True)
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
    # 探索固有
    p.add_argument("--search-param", action="append", default=[], required=False)
    # choices は usecase の唯一の登録表から導出する（ISSUE-101 🔵-1・順序保存）。
    from simulator.usecase.optimize_strategies import OBJECTIVE_REGISTRY, SEARCH_ALGOS

    p.add_argument("--search-algo", choices=list(SEARCH_ALGOS), required=True)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--n-samples", type=int, default=None)
    p.add_argument("--max-candidates", type=int, required=True)  # M-3 必須
    p.add_argument("--objective", choices=list(OBJECTIVE_REGISTRY), required=True)
    return p


def _coerce_scalar(v: str) -> Any:
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def _parse_config_overrides(items: "list[str]") -> dict:
    out: dict = {}
    for kv in items:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        out[k] = _coerce_scalar(v)
    return out


def _parse_search_space(items: "list[str]") -> dict:
    """`name=v1,v2,..` 反復 -> {name: [v1, v2, ...]}（入力順保持）。"""
    space: dict = {}
    for kv in items:
        if "=" not in kv:
            continue
        name, vals = kv.split("=", 1)
        space[name] = [_coerce_scalar(x) for x in vals.split(",")]
    return space


def _build_base_kwargs(args) -> dict:
    return dict(
        data_path=args.data_path,
        symbol=args.symbol,
        period=args.period,
        ea_name=args.ea_name,
        initial_deposit=args.initial_deposit,
        contract_size=args.contract_size,
        volume_min=args.volume_min,
        volume_max=args.volume_max,
        volume_step=args.volume_step,
        stops_level=args.stops_level,
        digits=args.digits,
        point_size=args.point_size,
        leverage=args.leverage,
        ma_period=args.ma_period,
        ma_method=args.ma_method,
        lot_size=args.lot_size,
        stop_loss_points=args.stop_loss_points,
        take_profit_points=args.take_profit_points,
        entry_offset_points=args.entry_offset_points,
        entry_type=args.entry_type,
        config_overrides=_parse_config_overrides(args.config_override),
        stop_out_level=args.stop_out_level,
    )


def main(argv: "list[str] | None" = None, *, repo_root: Any = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    out_dir = assert_safe_output_dir(args.out_dir, repo_root)  # SP1 再利用（先頭で検証）

    base_kwargs = _build_base_kwargs(args)
    search_space = _parse_search_space(args.search_param)
    factory, full_bars, split, is_start = make_run_segment_factory(
        base_kwargs, split_str=args.split, is_trading_start_str=args.is_trading_start
    )

    result = optimize(
        request=OptimizeRequest(
            search_space=search_space, split=split, is_trading_start=is_start
        ),
        full_bars=full_bars,
        make_run_segment=factory,
        search_port=_build_search_port(args),
        objective_port=_build_objective_port(args),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "optimize.json").write_text(
        json.dumps(to_json_dict(result), indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(
        to_markdown(
            result,
            split=args.split,
            is_trading_start=args.is_trading_start,
            objective=args.objective,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
