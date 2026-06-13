"""adapter/compute（内部設計書 §3.3.1-3.3.3）— 既存 add_* 隔離点（唯一）。

公開 API:
    FakeChart                           : line/histogram/horizontal_line を一括収集する統合スタブ。
    FakeLineChart / FakeHorizontalChart : line / horizontal_line 専用スタブ（後方互換）。
    CallBinding                         : 指標ごとの呼出規約（fitter 実体化・位置/キーワード二分）。
    IndicatorComputeAdapter             : compute(request) を実装し系列 JSON へ変換。
    ComputeError                        : §6.3.4 エラー翻訳例外。
"""

from __future__ import annotations

from adapter.compute.call_binding import CallBinding
from adapter.compute.fake_chart import FakeChart, FakeHorizontalChart, FakeLineChart
from adapter.compute.indicator_compute_adapter import (
    ERROR_STATUS,
    ComputeError,
    IndicatorComputeAdapter,
)

__all__ = [
    "FakeChart",
    "FakeLineChart",
    "FakeHorizontalChart",
    "CallBinding",
    "IndicatorComputeAdapter",
    "ComputeError",
    "ERROR_STATUS",
]
