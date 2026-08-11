"""A-IndicatorApiController: 指標一覧 API の HTTP 表現 ⇄ usecase の変換（adapter 層）。

責務（SRP）: **翻訳だけ**。HTTP の語彙（状態コード・JSON）と usecase の語彙（DTO・例外）の
間を写す。検定の規則も台帳の形式も持たない（それは usecase と adapter/ledger）。

応答の単位は**系列**（裁定 A）。1 件 = 1 系列で ``selectable`` と ``reason``（3 値固定）・
``detail``（自由文）を返す。選択不可の系列も必ず含める（無音で消さない）。測定条件
（``conditions``）を併せて返す（どの窓・どの許容差で測ったのか無しに一致主張は再現できない）。

例外 → 状態コードの対応:
    CausalityLedgerUnavailableError → 503（台帳が無い＝供給可否を答えられない）

**空一覧の 200 に倒さない**。空 200 は「検定した結果 0 件」と区別がつかず、未検定の状態を
「検定済み」と誤読させる。答えられないことは答えられないと返す（fail-closed）。

応答の JSON 直列化は既存 `job_api_controller.ApiResponse` を import 再利用する
（同型の to_bytes を書き直さない）。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.adapter.job_api_controller import ApiResponse
from simulator.sim_ui.usecase.indicator_models import (
    CausalityLedgerUnavailableError,
    IndicatorListing,
)
from simulator.sim_ui.usecase.indicator_ports import IndicatorCausalityLedgerPort
from simulator.sim_ui.usecase.list_indicators import list_indicators


class IndicatorApiController:
    """`GET /indicators` の入出力変換。"""

    def __init__(self, *, ledger: IndicatorCausalityLedgerPort) -> None:
        self._ledger = ledger

    @property
    def ledger(self) -> IndicatorCausalityLedgerPort:
        """一覧の出所（合成根の検定が実物の結線を確かめるための面）。"""
        return self._ledger

    def list(self) -> ApiResponse:
        try:
            listing = list_indicators(ledger=self._ledger)
        except CausalityLedgerUnavailableError as exc:
            return ApiResponse(503, {"error": str(exc)})
        return ApiResponse(200, _listing_payload(listing))


def _listing_payload(listing: IndicatorListing) -> "dict[str, Any]":
    conditions = listing.conditions
    return {
        "ok": True,
        "measured_at": listing.measured_at,
        "conditions": {
            "ref": conditions.ref,
            "timeframe": conditions.timeframe,
            "supply_bars": conditions.supply_bars,
            "verify_bars": conditions.verify_bars,
            "verify_coverage": conditions.verify_coverage,
            "timeout": conditions.timeout,
            "supply_budget": conditions.supply_budget,
            "limit": conditions.limit,
            "tolerance": conditions.tolerance,
            "probe_mode": conditions.probe_mode,
        },
        "series": [
            {
                "indicator": item.indicator,
                "variant": item.variant,
                "params": dict(item.params or {}),
                "series": item.series_name,
                "selectable": item.selectable,
                "reason": item.reason,
                "detail": item.detail,
            }
            for item in listing.items
        ],
    }
