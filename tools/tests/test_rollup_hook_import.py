"""回帰: export_jp225_m1 の rollup hook が repo 根 import 文脈で rollup_builder を解決する。

兄弟名 `import rollup_builder` はスクリプト実行（tools dir が sys.path[0]）でしか解決せず、
パイプライン（`tools/acquire_marketdata.py`）が repo 根から module import すると
ModuleNotFoundError になっていた。絶対 import への是正を、tools dir を sys.path から外した
文脈（＝パイプライン相当）で hook を呼んで検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = "/workspaces/app/indigators/indicator_ui/tools"


def test_rollup_hook_resolves_rollup_builder_without_tools_dir_on_path(monkeypatch, tmp_path):
    # パイプライン文脈を再現: tools dir を sys.path から除外（兄弟名 import なら失敗する状態）。
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p).as_posix() != _TOOLS_DIR])

    from indigators.indicator_ui.tools import rollup_builder as rb
    from indigators.indicator_ui.tools.export_jp225_m1 import build_rollup_hook

    called = {}

    class _FakeState:
        @staticmethod
        def load(out_dir):
            return None

    monkeypatch.setattr(rb, "RollupState", _FakeState)
    monkeypatch.setattr(rb, "incremental_update",
                        lambda *a, **k: called.__setitem__("ok", True))

    hook = build_rollup_hook(out_dir=tmp_path)
    hook(tmp_path / "jp225_m1.csv", 1)  # ここで rollup_builder の import が走る（解決必須）

    assert called.get("ok") is True
