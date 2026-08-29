"""宣言整合性検定の pytest 入口。

このテストは対象コードを import しない。AST のみを読むため、
collection error が残っている状態でも単独で実行できる。

    pytest .claude/scripts/test_declaration_integrity.py
"""

from __future__ import annotations

import json
from pathlib import Path

import declaration_integrity as di
import quality_scope
from declaration_integrity import run, _infer_prefixes

quality_scope.apply(di)

# this file: <repo>/.claude/scripts/ -> parents[2] = リポジトリ根。
REPO = Path(__file__).resolve().parents[2]
# baseline は test_static_quality.py と同一ファイルを見る（2 系統を作らない）。
BASELINE = Path(__file__).with_name("di_baseline.json")
CHECKS = {"C1", "C2", "C3"}


def _frozen() -> set[str]:
    if not BASELINE.exists():
        return set()
    return set(json.loads(BASELINE.read_text(encoding="utf-8")))


def test_no_new_declaration_violations() -> None:
    vs = run(REPO, _infer_prefixes(REPO), CHECKS)
    frozen = _frozen()
    new = [v for v in vs if v.ident() not in frozen]
    assert not new, "新規の宣言整合性違反:\n" + "\n".join(
        f"  {v.check} {v.path}:{v.line} {v.key} — {v.detail}" for v in new
    )


def test_baseline_is_not_stale() -> None:
    """解消済みの違反が baseline に残り続けることを防ぐ（後退の禁止）。"""
    vs = run(REPO, _infer_prefixes(REPO), CHECKS)
    stale = _frozen() - {v.ident() for v in vs}
    assert not stale, (
        f"baseline に解消済みの {len(stale)} 件が残っている。"
        " --write-baseline で更新する:\n  " + "\n  ".join(sorted(stale)[:20])
    )
