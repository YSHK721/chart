"""アーキ回帰（ISSUE-092 ②）: replay bridge の indicator_ui import 面を安定公開 Facade へ縮約。

bridge（``simulator/replay_ui/adapter/_indicator_ui_bridge.py``）は indicator_ui の compute を
安定公開 Facade ``adapter.compute``（``adapter/compute/__init__.py``）1 点からのみ参照し、
内部モジュール（``latest_dispatch`` / ``indicator_compute_adapter``）へは直接 import しない。
内部構成（モジュール名・配置）への密結合を構造的に禁止する回帰ガード。

test_replay_purity.py 流儀（ソースをファイルとして走査し構造を固定する）に合わせる。
"""
from __future__ import annotations

from pathlib import Path

import pytest

_BRIDGE = (
    Path(__file__).resolve().parents[2] / "adapter" / "_indicator_ui_bridge.py"
)

# indicator_ui の compute 内部モジュール名。Facade 経由（adapter.compute）に縮約後は
# bridge ソースへ一切現れてはならない（コメント含め全文で 0 件）。
_FORBIDDEN_INTERNAL_MODULES = ("latest_dispatch", "indicator_compute_adapter")


@pytest.mark.parametrize("module_name", _FORBIDDEN_INTERNAL_MODULES)
def test_bridge_does_not_reference_indicator_ui_internal_module(module_name):
    source = _BRIDGE.read_text(encoding="utf-8")
    assert module_name not in source, (
        f"bridge が indicator_ui 内部モジュール名 '{module_name}' を参照している。"
        f" 安定公開 Facade 'adapter.compute' 経由へ縮約すること（ISSUE-092 ②）。"
    )
