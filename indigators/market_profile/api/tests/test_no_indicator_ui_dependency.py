"""ISSUE-087 🔴-1: MP backend が indicator_ui の `adapter` パッケージへ裸名依存しない回帰ガード。

共有純粋物（tf メタ・tick ref・期間始端・ERROR_STATUS）は最下層 marketdata（tf_meta/api_contract）
に単一定義し、market_profile_api は marketdata のみを参照する（sys.path 注入前提の横断結合を排す）。
"""
from __future__ import annotations

import re
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "market_profile_api"


def test_no_bare_adapter_imports():
    offenders = []
    for p in _PKG.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if re.match(r"\s*(from|import)\s+adapter(\.|\s|$)", line):
                offenders.append(f"{p.relative_to(_PKG)}:{i}: {line.strip()}")
    assert not offenders, "indicator_ui の adapter への裸依存が残存:\n" + "\n".join(offenders)
