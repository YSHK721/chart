"""ISSUE-092 ①: usecase 層が marketdata / adapter を module-level import しない回帰ガード。

usecase（内側＝Application Business Rules）は偶有的性質（marketdata の物理格納・adapter の
具象アダプタ）を module-level に取り込まない。外部依存は Output Boundary（DatasetPort）と
呼出時注入（forming_bar / full_compute / latest_compute / compute_error / compute_adapter）
に限定し、依存方向を「外側 → 内側」に保つ。

様式は market_profile_api/tests/test_no_indicator_ui_dependency.py を踏襲。

ISSUE-183（ガード強化）: 旧版は **module-level（非インデント）** の import のみを禁止し、
``dataset_port()`` 内の関数スコープ gateway 合成（``from adapter.gateway.composition import
default_dataset_port``）を「DI シーム」として明示的に許容していた。しかし関数スコープでも
ソースコード依存の向きは内側 → 外側であり、Dependency Rule 違反であることに変わりはない
（遅延させても方向は変わらない）。既定合成は composition root からの push
（``install_default_ports()`` → ``set_default_dataset_port_factory``）へ反転済みのため、
本ガードは **インデントの有無を問わず** usecase 配下の全 import 文を禁止対象にする。
"""
from __future__ import annotations

import re
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "usecase"

# インデントの有無を問わず、from/import で marketdata または adapter を取り込む行を禁止する
#   （ISSUE-183: 関数スコープの遅延 import も逆流として扱う）。
_FORBIDDEN = re.compile(r"^\s*(from|import)\s+(marketdata|adapter)(\.|\s|$)")


def test_usecase_has_no_marketdata_or_adapter_imports_at_any_scope():
    offenders = []
    for p in _PKG.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if _FORBIDDEN.match(line):
                offenders.append(f"{p.relative_to(_PKG)}:{i}: {line.strip()}")
    assert not offenders, (
        "usecase の marketdata/adapter 依存が残存（関数スコープの遅延 import を含む）:\n"
        + "\n".join(offenders)
    )
