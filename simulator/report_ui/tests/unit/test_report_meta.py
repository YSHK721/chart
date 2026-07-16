"""ReportMeta（特定実験の所与オブジェクト）単体テスト（ISSUE-094 🟡-5）。

既定値が現行 StopEntryProbe 実験値であること（byte 不変の源）と、値注入で上書き可能
（EA 非依存化）であることを固定する。
"""
from __future__ import annotations

from simulator.report_ui.usecase.report_meta import ReportMeta


def test_defaults_match_current_experiment_values():
    m = ReportMeta()
    assert m.expert == "StopEntryProbe_EA"
    assert m.params == "ProbeDir=2(両建て) / offset100 / Lot0.1 / SL200 / TP500"
    assert m.split == "2026-04-15"
    assert m.note == "IS/OOS 単純分割（同一パラメータを両区間で評価・最適化なし）"
    assert m.symbol == "JP225"
    assert m.timeframe == "M1"


def test_fields_are_injectable_for_ea_agnostic_reuse():
    m = ReportMeta(expert="OtherEA", params="p", split="2030-01-01",
                   note="n", symbol="US500", timeframe="M5")
    assert m.expert == "OtherEA"
    assert m.symbol == "US500"
    assert m.timeframe == "M5"


def test_is_frozen():
    import dataclasses
    m = ReportMeta()
    try:
        m.expert = "X"  # frozen → 例外
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised
