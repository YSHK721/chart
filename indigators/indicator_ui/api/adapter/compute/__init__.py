"""adapter/compute（内部設計書 §3.3.1-3.3.3）— 既存 add_* 隔離点（唯一）＋安定公開 Facade。

公開 API:
    FakeChart                           : line/histogram/horizontal_line を一括収集する統合スタブ。
    FakeLineChart / FakeHorizontalChart : line / horizontal_line 専用スタブ（後方互換）。
    CallBinding                         : 指標ごとの呼出規約（fitter 実体化・位置/キーワード二分）。
    IndicatorComputeAdapter             : compute(request) を実装し系列 JSON へ変換。
    ComputeError                        : §6.3.4 エラー翻訳例外。
    full_compute / latest_compute       : /compute 境界の全件/末尾K計算ディスパッチ（latest_dispatch）。

安定公開 Facade（ISSUE-092 ②）:
    スライス外（replay bridge 等）からの indicator_ui compute 参照は、本 Facade
    ``adapter.compute`` のみを契約とする。内部モジュール（``latest_dispatch`` /
    ``indicator_compute_adapter`` 等）への直接 import は禁止する。内部構成（モジュール名・
    配置）は本 Facade の背後に隠蔽し、再エクスポートするシンボルのみを公開契約とする。
"""

from __future__ import annotations

from adapter.compute.call_binding import CallBinding
from adapter.compute.catalog_schema import (
    PARAM_DEFAULTS,
    PARAM_SCOPES,
    catalog_defaults,
    catalog_param_scopes,
)
from adapter.compute.fake_chart import FakeChart, FakeHorizontalChart, FakeLineChart
from adapter.compute.indicator_compute_adapter import (
    ERROR_STATUS,
    ComputeError,
    IndicatorComputeAdapter,
)
from adapter.compute.latest_dispatch import full_compute, latest_compute

__all__ = [
    "FakeChart",
    "FakeLineChart",
    "FakeHorizontalChart",
    "CallBinding",
    "PARAM_DEFAULTS",
    "PARAM_SCOPES",
    "catalog_defaults",
    "catalog_param_scopes",
    "IndicatorComputeAdapter",
    "ComputeError",
    "ERROR_STATUS",
    "full_compute",
    "latest_compute",
]
