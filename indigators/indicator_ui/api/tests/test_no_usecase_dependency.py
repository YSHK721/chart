"""ISSUE-092 ①: usecase 層が marketdata / adapter を module-level import しない回帰ガード。

usecase（内側＝Application Business Rules）は偶有的性質（marketdata の物理格納・adapter の
具象アダプタ）を module-level に取り込まない。外部依存は Output Boundary（DatasetPort）と
呼出時注入（forming_bar / full_compute / latest_compute / compute_error / compute_adapter）
に限定し、依存方向を「外側 → 内側」に保つ。

様式は market_profile_api/tests/test_no_indicator_ui_dependency.py を踏襲。ただし本ガードは
ISSUE-092 ① の要件どおり **module-level（非インデント）** の import のみを禁止する。未注入時の
遅延既定（dataset_port() 内の関数レベル gateway 合成）は DI シームとして許容する。
"""
from __future__ import annotations

import re
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "usecase"

# 行頭（インデントなし）の from/import で marketdata または adapter を取り込む行を禁止する。
_FORBIDDEN = re.compile(r"^(from|import)\s+(marketdata|adapter)(\.|\s|$)")


def test_usecase_has_no_module_level_marketdata_or_adapter_imports():
    offenders = []
    for p in _PKG.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if _FORBIDDEN.match(line):
                offenders.append(f"{p.relative_to(_PKG)}:{i}: {line.strip()}")
    assert not offenders, (
        "usecase の module-level marketdata/adapter 依存が残存:\n" + "\n".join(offenders)
    )
