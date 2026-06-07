"""adapter/compute（内部設計書 §3.3.1-3.3.3）— 既存 add_* 隔離点（唯一）。

公開 API:
    FakeLineChart / FakeHorizontalChart : 描画せず系列仕様を収集する duck typing スタブ。
    CallBinding                         : 指標ごとの呼出規約（fitter 実体化・位置/キーワード二分）。
    IndicatorComputeAdapter             : compute(request) を実装し系列 JSON へ変換。
    ComputeError                        : §6.3.4 エラー翻訳例外。
"""

from __future__ import annotations

from adapter.compute.call_binding import CallBinding
from adapter.compute.fake_chart import FakeHorizontalChart, FakeLineChart
from adapter.compute.indicator_compute_adapter import (
    ComputeError,
    IndicatorComputeAdapter,
)

__all__ = [
    "FakeLineChart",
    "FakeHorizontalChart",
    "CallBinding",
    "IndicatorComputeAdapter",
    "ComputeError",
]
