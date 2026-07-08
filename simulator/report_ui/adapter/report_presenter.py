"""ReportUiPresenter（ReportPayloadPresenterPort 実装・詳細設計 §5）。

報告ドメインモデル（ReportPayloadModel）を JSON 契約 dict へ純変換し report.json へ書出す。
inf/-inf/nan は _sanitize で null に正規化し、json.dump(allow_nan=False) で
ブラウザ JSON.parse 互換の JSON を出力する（§5.5）。

adapter 層は usecase + domain + stdlib(json) のみに依存する。
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from typing import Any

from simulator.report_ui.usecase.report_models import (
    ReportPayloadModel,
    SegmentModel,
    SummaryModel,
)
from simulator.report_ui.usecase.report_ports import ReportPayloadPresenterPort


def _sanitize(o: Any) -> Any:
    """inf/-inf/nan を None に正規化する再帰純関数（§5.5）。"""
    if isinstance(o, float):
        return None if (math.isinf(o) or math.isnan(o)) else o
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    return o


class ReportUiPresenter(ReportPayloadPresenterPort):
    """ReportPayloadModel → report.json（JSON 契約）への変換・書出。"""

    def present_report_payload(self, payload_model: Any, path: Any) -> None:
        contract = self._to_contract(payload_model)
        clean = _sanitize(contract)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)

    # --- Model → 契約 dict ---------------------------------------------------

    def _to_contract(self, p: ReportPayloadModel) -> dict:
        return {
            "meta": p.meta,
            "segments": {k: self._segment(v) for k, v in p.segments.items()},
            "summary": {k: self._summary(v) for k, v in p.summary.items()},
            "degradation": p.degradation,
            "verdict": {"result": p.verdict.result, "reasons": list(p.verdict.reasons)},
            "_contract_notes": list(p.contract_notes),
        }

    def _segment(self, seg: SegmentModel) -> dict:
        return {
            "label": seg.label,
            "meta": seg.meta,
            "report": seg.report,
            "bars": seg.bars,
            "trades": [self._row(t) for t in seg.trades],
            "orders": [self._row(o) for o in seg.orders],
            "agg": seg.agg,
        }

    def _summary(self, s: SummaryModel) -> dict:
        return asdict(s)

    def _row(self, row: Any) -> dict:
        # TradeRow / OrderRow は dataclass。dict ならそのまま通す。
        if is_dataclass(row) and not isinstance(row, type):
            return asdict(row)
        return row
