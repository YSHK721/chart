"""edge_ruin の JS golden fixture と権威の同期検定（ISSUE-368 スライス 1）。

設計入力（唯一の仕様源）: simulator/tools/export_edge_ruin_fixtures.py（生成器）と
    simulator/usecase/edge_ruin.py（権威）。
このテストが固定する回帰: 追跡下の js_golden_cases.json が現行の権威から再生成した値と
    一致していること。権威式・PRNG・乱数消費順を変更したのに fixture を再生成し忘れると
    本テストが落ちる（JS 側が古い正解で検定される事故の遮断）。
    `test_account_engine_js_fixture_sync.py:22-35` と同型。
"""
from __future__ import annotations

import json

from simulator.tools import export_edge_ruin_fixtures as gen


def test_committed_fixture_matches_current_authority():
    committed = json.loads(gen.OUT.read_text(encoding="utf-8"))
    assert committed["cases"], "ケースが空"
    current = gen.build_payload()
    assert len(committed["cases"]) == len(current["cases"]), (
        "ケース数が生成器と一致しない。simulator/tools/export_edge_ruin_fixtures.py を"
        " 再実行して fixture を更新してください")
    for got, want in zip(committed["cases"], current["cases"]):
        assert got == want, (
            f"{want['id']} が現行の権威と一致しない。"
            " simulator/tools/export_edge_ruin_fixtures.py を再実行してください")
    assert committed["tolerance"] == current["tolerance"]


def test_reference_default_case_uses_reference_sims():
    """参照実装既定（SIMS=4000）のケースが 1 件だけ載っていること（設計書 スライス 1）。"""
    committed = json.loads(gen.OUT.read_text(encoding="utf-8"))
    full = [c for c in committed["cases"] if c["spec"]["sims"] == gen.SIMS]
    assert len(full) == 1, f"SIMS={gen.SIMS} のケースは 1 件のみ: {[c['id'] for c in full]}"
    assert full[0]["spec"] == {
        "win_rate": 0.38, "payoff_ratio": 2.74, "ruin_level": 0.5, "alpha": 0.01,
        "horizon": 250, "split_count": 20, "seed": 1, "sims": gen.SIMS,
    }
