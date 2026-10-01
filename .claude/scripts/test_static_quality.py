"""静的品質検定の pytest 入口（宣言整合性 C1-C3 / テスト品質 T1-T8）。

対象コードを import しない。AST のみを読むため、collection error が残る状態でも
単独で実行できる。

    pytest .claude/scripts/test_static_quality.py

baseline の更新（解消分の除去は削除専用の prune を使う）:

    python3 .claude/scripts/run_quality_gate.py --prune-baseline
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# 違反集合の定義（走査・フィルタ・baseline の対応）は run_quality_gate.SUITES が唯一持つ。
# 本ファイルが生の di.run / tq.run を別に組むと「違反集合」の第 2 定義になり、gate が
# 落とす C1（散文引用・実在パス）をこちらだけが数えて、gate 緑・pytest 赤が恒常化する
# （ISSUE-543 の裁定で単一ソース化。実測: HEAD の旧構成は 3.14 でも 4 件赤だった）。
import run_quality_gate as gate

HERE = Path(__file__).parent
SUITES = gate.SUITES


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
        f"[{suite}] baseline に解消済みの {len(stale)} 件が残存。"
        f"run_quality_gate.py --prune-baseline で除去する:\n  "
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
