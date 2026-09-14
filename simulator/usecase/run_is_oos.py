"""UC: IS/OOS 単純分割オーケストレーション（基本設計 v0.2.0 / FR-01..06）。

committed エンジンを無改変で IS 区間 [start, split) と OOS 区間 [split, end) で別々に
実行し、両 BacktestStats と劣化指標を返す。エンジン実行手段は run_segment コールバック
として呼出側（tools 層）から注入する（DIP・usecase→domain のみ依存）。

usecase は domain のみ依存（adapter/framework/main・pandas を import しない）。
時刻は domain と同じく numpy.datetime64 | int（pd.Timestamp を前提にしない）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class IsOosValidationError(Exception):
    """IS/OOS 区間の事前検証 NG（区間空・範囲不正）。エンジン呼出前に送出する。"""


# 型エイリアス（ドキュメント用）。run_segment は UC が IS/OOS 各区間を実行する手段。
#   引数: bars（full or IS-truncated）, trading_start（IS=is_trading_start / OOS=split）
#   戻り値: BacktestStats（当該区間の成績）
RunSegment = Callable[[Any, Any], Any]


def slice_is_bars(bars: Any, split: Any) -> list:
    """IS 区間用に bars の head 区間（bar.time < split）を返す純関数（B-2/H-3）。

    bar.time < split を保持し、最初に bar.time >= split を満たした位置で打ち切る
    （head-prefix・中抜き/tail 切りなし）。split は OOS 側（半開区間 [split, end)）。
    副作用なし（入力 bars を破壊しない・新規 list を返す）。
    """
    result: list = []
    for bar in bars:
        if bar.time < split:
            result.append(bar)
        else:
            break  # 時刻昇順前提：最初の split 到達で head 区間は確定
    return result


@dataclass
class RunIsOosRequest:
    """単純分割の入力（基本設計 §6.1・§5.2）。"""

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
class MetricDegradation:
    """1 指標の IS/OOS 劣化（C-2: ratio・delta 両格納）。"""

    name: str
    is_value: float
    oos_value: float
    ratio: "float | None"
    delta: float


@dataclass
class DegradationReport:
    """全主要指標の劣化集合（C-2）。"""

    metrics: "list[MetricDegradation]" = field(default_factory=list)

    def by_name(self, name: str) -> "MetricDegradation | None":
        for m in self.metrics:
            if m.name == name:
                return m
        return None


@dataclass
class RunIsOosResult:
    """単純分割の出力一式（基本設計 §5.2 IsOosResult）。"""

    is_stats: Any
    oos_stats: Any
    degradation: DegradationReport


def extract_metrics(stats: Any, names: "tuple[str, ...]") -> "dict[str, float]":
    """BacktestStats から劣化対象指標を name->値 で抽出する（後段② ObjectivePort 前身）。"""
    return {n: float(getattr(stats, n)) for n in names}


def build_degradation_report(
    is_stats: Any, oos_stats: Any, names: "tuple[str, ...]"
) -> DegradationReport:
    """IS/OOS の BacktestStats から ratio・delta を両格納した劣化レポートを構築（C-2）。"""
    is_m = extract_metrics(is_stats, names)
    oos_m = extract_metrics(oos_stats, names)
    metrics = []
    for n in names:
        iv = is_m[n]
        ov = oos_m[n]
        ratio = (ov / iv) if iv != 0.0 else None
        metrics.append(
            MetricDegradation(name=n, is_value=iv, oos_value=ov, ratio=ratio, delta=ov - iv)
        )
    return DegradationReport(metrics=metrics)


def run_is_oos(
    *,
    request: RunIsOosRequest,
    full_bars: Any,
    run_segment: RunSegment,
) -> RunIsOosResult:
    """IS/OOS を 2 回実行し劣化指標つき結果を返す（FR-01..06）。

    処理: (1) 検証（範囲・空区間 M-1） (2) is_bars=slice_is_bars (3) IS run
          (4) OOS run (5) 劣化算出 (6) RunIsOosResult。
    例外: IsOosValidationError（区間空・範囲不正）。
    """
    full_list = list(full_bars)
    is_bars = slice_is_bars(full_list, request.split)
    oos_count = sum(1 for b in full_list if b.time >= request.split)
    if len(is_bars) < 1:
        raise IsOosValidationError("IS 区間が空（bar.time < split を満たすバーが 0 件）")
    if oos_count < 1:
        raise IsOosValidationError("OOS 区間が空（bar.time >= split を満たすバーが 0 件）")
    if not (request.is_trading_start <= request.split):
        raise IsOosValidationError("is_trading_start は split 以下である必要がある")
    is_stats = run_segment(is_bars, request.is_trading_start)
    oos_stats = run_segment(full_list, request.split)
    degradation = build_degradation_report(is_stats, oos_stats, request.metric_names)
    return RunIsOosResult(is_stats=is_stats, oos_stats=oos_stats, degradation=degradation)
