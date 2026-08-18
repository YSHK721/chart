"""run_is_oos_cli.py — IS/OOS 単純分割の実行入口（詳細設計 §2.2）。

責務＝結線（Composition Root 利用側・committed 無改変＝C2/C3）:
  1. CLI 引数解釈（argparse）。
  2. build_interactor(...) で (controller, request) を構築（committed 公開 IF のみ）。
  3. make_run_segment で run_segment コールバックを構成（controller.execute 経由・B-1）。
  4. normalize_time で split/is_trading_start を bar.time 型へ正規化（pandas は tools 層に閉じる）。
  5. run_is_oos(...) を呼ぶ。
  6. assert_safe_output_dir → to_json_dict / to_markdown → 新規 OUT 書込。

pandas・simulator.main の import は tools 層では許容（Composition Root 利用側）。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from simulator.main import build_interactor
from simulator.usecase.run_is_oos import RunIsOosRequest, run_is_oos


class OutputGuardError(Exception):
    """書込先が既存データディレクトリ配下・repo_root 外（C1/NFR-S1・H-2 違反）。"""


_FORBIDDEN_PREFIXES = (
    "marketdata",
    "simulator/tests/fixtures",
    "simulator/tests/confirmation",
)


def make_run_segment(controller: Any, request: Any) -> Callable[[Any, Any], Any]:
    """build_interactor が返した (controller, request) を閉包し run_segment を構成（B-1）。

    区間ごとに bars/trading_start のみ差し替えて controller.execute を呼ぶ。
    controller.run / run_backtest は使わない（再ロードで truncation 無効化されるため）。
    """

    def run_segment(bars: Any, trading_start: Any) -> Any:
        request.bars = bars
        request.trading_start = trading_start
        result = controller.execute(request)
        return result.stats

    return run_segment


def normalize_time(value: str, sample_bar_time: Any) -> Any:
    """CLI 文字列を bar.time と比較可能な型へ正規化（tools 層に pandas を閉じる・TBD-5）。

    sample_bar_time が numpy.datetime64 系なら pd.Timestamp(value).to_datetime64()、
    epoch int 系なら int(pd.Timestamp(value).timestamp()) を返す。
    """
    ts = pd.Timestamp(value)
    if isinstance(sample_bar_time, (int,)) and not isinstance(sample_bar_time, bool):
        return int(ts.timestamp())
    # numpy.datetime64（既定の Mt5CsvOHLCRepository / CsvOHLCRepository の bar.time 型）
    return ts.to_datetime64()


def assert_safe_output_dir(out_dir: str, repo_root: Any) -> Path:
    """書込先が既存データディレクトリ配下でないことを検証する純関数（C1/NFR-S1・H-2）。

    戻り値: 解決済み絶対 Path（許可された場合）。
    例外  : OutputGuardError（禁止プレフィクス配下・repo_root 外）。
    """
    root = Path(repo_root).resolve()
    resolved = (root / out_dir).resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        raise OutputGuardError(f"repo_root 外への書込は禁止: {resolved}")
    rel_posix = rel.as_posix()
    for pref in _FORBIDDEN_PREFIXES:
        if rel_posix == pref or rel_posix.startswith(pref + "/"):
            raise OutputGuardError(f"既存データディレクトリ配下への書込は禁止: {rel_posix}")
    return resolved


def to_json_dict(result: Any) -> dict:
    """RunIsOosResult を JSON シリアライズ可能な dict へ（asdict パターン）。"""
    return {
        "is_stats": asdict(result.is_stats),
        "oos_stats": asdict(result.oos_stats),
        "degradation": [asdict(m) for m in result.degradation.metrics],
    }


def to_markdown(result: Any, *, split: Any = None, is_trading_start: Any = None) -> str:
    """IS 列 | OOS 列 | ratio | delta の並列レポート（人間可読）。"""
    lines = [
        "# IS/OOS Simple Split Report",
        "",
        f"- split: {split}",
        f"- is_trading_start: {is_trading_start}",
        "",
        "## Summary",
        "| metric | IS | OOS | ratio (OOS/IS) | delta (OOS-IS) |",
        "|---|---|---|---|---|",
    ]
    for m in result.degradation.metrics:
        ratio = "N/A" if m.ratio is None else f"{m.ratio:.3f}"
        lines.append(
            f"| {m.name} | {m.is_value} | {m.oos_value} | {ratio} | {m.delta} |"
        )
    return "\n".join(lines) + "\n"


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IS/OOS 単純分割の実行入口")
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
    return p


def _parse_config_overrides(items: "list[str]") -> dict:
    out: dict = {}
    for kv in items:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        low = v.lower()
        if low in ("true", "false"):
            out[k] = low == "true"
        else:
            try:
                out[k] = int(v)
            except ValueError:
                try:
                    out[k] = float(v)
                except ValueError:
                    out[k] = v
    return out


def main(argv: "list[str] | None" = None, *, repo_root: Any = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    out_dir = assert_safe_output_dir(args.out_dir, repo_root)

    controller, request = build_interactor(
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
    sample_time = request.bars[0].time
    split = normalize_time(args.split, sample_time)
    is_trading_start = normalize_time(args.is_trading_start, sample_time)
    run_segment = make_run_segment(controller, request)

    result = run_is_oos(
        request=RunIsOosRequest(split=split, is_trading_start=is_trading_start),
        full_bars=request.bars,
        run_segment=run_segment,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "is_oos.json").write_text(
        json.dumps(to_json_dict(result), indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(
        to_markdown(result, split=args.split, is_trading_start=args.is_trading_start),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
