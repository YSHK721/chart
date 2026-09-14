"""JS golden fixture と権威（閉形式）の同期検定（ISSUE-369 Phase 2）。

設計入力（唯一の仕様源）: simulator/tools/export_account_engine_fixtures.py（生成器）と
    simulator/usecase/account_engine.py の official_* 閉形式。
このテストが固定する回帰: 追跡下の js_golden_cases.json が現行の権威式から再生成した値と
    一致していること。式を変更したのに fixture を再生成し忘れると本テストが落ちる
    （JS 側が古い正解で検定される事故の遮断）。
"""
from __future__ import annotations

import json
from pathlib import Path

from simulator.usecase.account_engine import (
    official_losscut_price, official_required_margin,
)

_FIXTURE = (Path(__file__).resolve().parents[1]
            / "fixtures" / "account_engine" / "js_golden_cases.json")


def test_js_golden_cases_match_authority_formulas():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert payload["cases"], "ケースが空"
    for case in payload["cases"]:
        entries = [(e["price"], e["units"]) for e in case["entries"]]
        req = official_required_margin(entries, case["margin_rate"])
        x = official_losscut_price(case["direction"], entries,
                                   case["balance"], case["margin_rate"])
        exp = case["expected"]
        assert exp["required_margin"] == req, case["id"]
        assert exp["losscut_price"] == x, case["id"]
        assert exp["margin_use"] == req / case["balance"], case["id"]
        avg_p = sum(p * u for p, u in entries) / sum(u for _, u in entries)
        assert exp["losscut_distance"] == abs(avg_p - x), case["id"]
