#!/usr/bin/env python3
"""静的品質検定の Stop フック入口 — 新規違反だけを報せる。

`declaration_integrity.py`（C1-C3）と `test_quality.py`（T1-T8）を、baseline で凍結した
既存違反を除いて走らせる。**新規違反が 0 なら何も出さない**。

契機はターン終了（Stop）。走査範囲は `quality_scope.PROJECT_EXCLUDE` が唯一の定義。

終了コード:
    0  新規違反なし（または baseline 未生成＝初回）
    2  新規違反あり（`asyncRewake` がモデルを起こす）

baseline の更新:
    python3 .claude/scripts/run_quality_gate.py --write-baseline

**baseline は「今の違反を許す」ためのものであって、増やしてよいという意味ではない。**
解消したら書き直す（`test_static_quality.py` が古い baseline を赤で落とす）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import declaration_integrity as di  # noqa: E402
import test_quality as tq  # noqa: E402
import quality_scope  # noqa: E402

quality_scope.apply(di, tq)

SUITES = {
    "declaration": (HERE / "di_baseline.json",
                    lambda: di.run(REPO, di._infer_prefixes(REPO), {"C1", "C2", "C3"})),
    "test_quality": (HERE / "tq_baseline.json",
                     lambda: tq.run(REPO, set(tq.ALL_CHECKS))),
}


def _frozen(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="静的品質検定（Stop フック入口）")
    ap.add_argument("--write-baseline", action="store_true",
                    help="現在の違反を baseline へ凍結し直す")
    ap.add_argument("--verbose", action="store_true", help="人間向けに全文を出す")
    a = ap.parse_args(argv)

    lines: list[str] = []
    for name, (baseline, runner) in SUITES.items():
        vs = runner()
        if a.write_baseline:
            baseline.write_text(
                json.dumps(sorted(v.ident() for v in vs), ensure_ascii=False, indent=1),
                encoding="utf-8")
            print(f"{name}: {len(vs)} 件を凍結 -> {baseline.name}")
            continue
        frozen = _frozen(baseline)
        if not frozen:
            continue                      # 初回（baseline 未生成）は何も言わない
        new = [v for v in vs if v.ident() not in frozen]
        for v in new[:10]:
            lines.append(f"  {v.check} {v.path}:{v.line} {v.key} — {v.detail}")
        if len(new) > 10:
            lines.append(f"  … 他 {len(new) - 10} 件")

    if a.write_baseline:
        return 0
    if not lines:
        return 0

    msg = "静的品質検定: 新規違反 " + str(len(lines)) + " 件\n" + "\n".join(lines)
    if a.verbose:
        print(msg)
    else:
        print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
