"""静的品質検定の pytest 入口（宣言整合性 C1-C3 / テスト品質 T1-T8）。

対象コードを import しない。AST のみを読むため、collection error が残る状態でも
単独で実行できる。

    pytest .claude/scripts/test_static_quality.py

baseline の更新:

    .claude/scripts/refresh_quality_baseline.sh
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import declaration_integrity as di
import test_quality as tq
import quality_scope

quality_scope.apply(di, tq)

# this file: <repo>/.claude/scripts/ -> parents[2] = リポジトリ根。
REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent

SUITES = {
    "declaration": (
        HERE / "di_baseline.json",
        lambda: di.run(REPO, di._infer_prefixes(REPO), {"C1", "C2", "C3"}),
    ),
    "test_quality": (
        HERE / "tq_baseline.json",
        lambda: tq.run(REPO, set(tq.ALL_CHECKS)),
    ),
}


def _frozen(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_no_new_violations(suite: str) -> None:
    baseline, runner = SUITES[suite]
    vs = runner()
    new = [v for v in vs if v.ident() not in _frozen(baseline)]
    assert not new, f"[{suite}] 新規違反 {len(new)} 件:\n" + "\n".join(
        f"  {v.check} {v.path}:{v.line} {v.key} — {v.detail}" for v in new[:40]
    )


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_baseline_is_not_stale(suite: str) -> None:
    """解消済み違反の baseline 残留を禁じる。凍結件数は単調減少しかできない。"""
    baseline, runner = SUITES[suite]
    stale = _frozen(baseline) - {v.ident() for v in runner()}
    assert not stale, (
        f"[{suite}] baseline に解消済みの {len(stale)} 件が残存。--write-baseline で更新する:\n  "
        + "\n  ".join(sorted(stale)[:20])
    )


def test_ratchet_is_monotonic() -> None:
    """凍結件数の上限を宣言し、増加を機械的に禁じる。

    値は導入時の実測で確定させ、以後は減らす方向にのみ更新する。
    """
    limits = json.loads((HERE / "ratchet.json").read_text(encoding="utf-8")) \
        if (HERE / "ratchet.json").exists() else {}
    for suite, (baseline, _) in SUITES.items():
        cap = limits.get(suite)
        if cap is None:
            continue
        n = len(_frozen(baseline))
        assert n <= cap, f"[{suite}] 凍結 {n} 件が上限 {cap} を超えた"
