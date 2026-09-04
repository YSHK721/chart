"""adapter/controller（内部設計書 §3.3.5）— HTTP 受け口の純ロジック層。

公開 API:
    handle_compute : POST /compute の純ロジック（HTTP の殻に依存しない）。
                     datasetRef ホワイトリスト解決 → IndicatorComputeAdapter 呼出 →
                     (HTTPステータス, ボディ) へ翻訳（§6.3.4 / §7.4）。
"""

from __future__ import annotations

from adapter.controller.compute_controller import handle_compute

__all__ = ["handle_compute"]
