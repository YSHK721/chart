"""split_entry_plan の JS golden fixture と権威の同期検定（ISSUE-368 スライス 1）。

設計入力（唯一の仕様源）: simulator/tools/export_split_entry_fixtures.py（生成器）と
    simulator/usecase/split_entry_plan.py（権威）。
このテストが固定する回帰: 追跡下の js_golden_cases.json が現行の権威から再生成した値と
    一致していること。権威式を変更したのに fixture を再生成し忘れると本テストが落ちる。
    `test_account_engine_js_fixture_sync.py:22-35` と同型。
"""
from __future__ import annotations

import json

from simulator.tools import export_split_entry_fixtures as gen


def test_committed_fixture_matches_current_authority():
    committed = json.loads(gen.OUT.read_text(encoding="utf-8"))
    assert committed["cases"], "ケースが空"
    current = gen.build_payload()
    assert len(committed["cases"]) == len(current["cases"]), (
        "ケース数が生成器と一致しない。simulator/tools/export_split_entry_fixtures.py を"
        " 再実行して fixture を更新してください")
    for got, want in zip(committed["cases"], current["cases"]):
        assert got == want, (
            f"{want['id']} が現行の権威と一致しない。"
            " simulator/tools/export_split_entry_fixtures.py を再実行してください")


def test_fixture_covers_every_branch():
    """4 分岐（stop_invalid / round_zeroed / immediate_lc / margin_binds）と
    cap_lot=Infinity が真になるケースが fixture に存在する（検定が空振りしない）。"""
    committed = json.loads(gen.OUT.read_text(encoding="utf-8"))
    for flag in ("stop_invalid", "round_zeroed", "immediate_lc", "margin_binds"):
        assert any(c["expected"][flag] for c in committed["cases"]), f"{flag} が真のケースが無い"
        assert any(not c["expected"][flag] for c in committed["cases"]), f"{flag} が偽のケースが無い"
    assert any(c["expected"]["cap_lot"] == gen.INFINITY_TOKEN for c in committed["cases"]), \
        "cap_lot=Infinity のケースが無い"
    assert any(c["expected"]["rr"] is not None for c in committed["cases"]), "利確ありのケースが無い"
    assert any(c["expected"]["rr"] is None for c in committed["cases"]), "利確なしのケースが無い"
