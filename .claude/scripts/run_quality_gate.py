#!/usr/bin/env python3
"""静的品質検定の Stop フック入口 — 新規違反だけを報せる。

`declaration_integrity.py`（C1-C3）と `test_quality.py`（T1-T8）を、baseline で凍結した
既存違反を除いて走らせる。**新規違反が 0 なら何も出さない**。

契機はターン終了（Stop）。走査範囲は `quality_scope.PROJECT_EXCLUDE` が唯一の定義。

終了コード:
    0  新規違反なし（または baseline 未生成＝初回）
    2  新規違反あり（`asyncRewake` がモデルを起こす）

baseline の更新:
    python3 .claude/scripts/run_quality_gate.py --prune-baseline   # 解消分の除去（削除専用・安全）
    python3 .claude/scripts/run_quality_gate.py --write-baseline   # 全再凍結（人間の裁定時のみ）

**baseline は「今の違反を許す」ためのものであって、増やしてよいという意味ではない。**
解消したら --prune-baseline で除去する（`test_static_quality.py` が古い baseline を赤で落とす）。
凍結件数の上限は `ratchet.json` が持ち、増加は `test_ratchet_is_monotonic` が機械的に禁じる。

散文の引用（ISSUE-532 欠陥 4・2026-09-25）:
    C1 は「バッククォートで名指した記号はそのモジュールから到達可能であること」を要求する。
    規則は正しいが、**設計の経緯を散文で書くだけ**でも import を要求されていた（1 セッションで
    10 回連続して発生）。そこで引用のための書式を設ける——鉤括弧で包んだ 「`name`」 は
    **散文の引用**として C1 の到達可能性を要求しない。包まずに書いた `name` は従来どおり
    **コード上の参照**であり、到達不能なら落ちる。ファイル内に 1 つでも包まれていない出現が
    あれば落ちる（引用を 1 つ足すとファイル全体が免除される、という緩みを作らない）。
    判定はファイル単位であり、これは C1 自身の粒度（同一ファイル・同一記号は 1 件）と同じ。
    施行の検定は `tools/tests/test_quality_gate_prose_quotation.py`（終了コードを直接測る）。

名指されたファイルの実在（同 ISSUE・同じ型の誤り）:
    C1 のパス分岐は実在を走査索引で代理していたため、走査から除外したディレクトリ
    （`.claude` 等・除外の理由は `quality_scope.py`）の**実在**ファイルを名指すと
    「存在しない」と報告された。実在はファイルシステムに問う。走査範囲の除外は
    「そこの違反を探さない」だけに効かせ、別の問いへ流用しない。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import declaration_integrity as di  # noqa: E402
import test_quality as tq  # noqa: E402
import quality_scope  # noqa: E402

quality_scope.apply(di, tq)

QUOTE_OPEN, QUOTE_CLOSE = "「", "」"


def is_prose_quotation(text: str, token: str) -> bool:
    """``text`` 内の ``token`` のバッククォート出現が**すべて**引用（鉤括弧）で包まれているか。

    1 つでも包まれていない出現（＝コード上の参照）が残れば False を返す。出現が無い場合も
    False（免除の根拠が無いため、既定は従来どおり落とす側に倒す）。
    """
    spans = re.finditer(r"(?<!`)(?:`+)" + re.escape(token) + r"(?:`+)(?!`)", text)
    wrapped = [
        text[m.start() - 1:m.start()] == QUOTE_OPEN and text[m.end():m.end() + 1] == QUOTE_CLOSE
        for m in spans
    ]
    return bool(wrapped) and all(wrapped)


def source_reader(root: Path):
    """``path``（root 相対）-> ソース文字列。同じファイルは 1 回しか読まない。"""
    cache: dict[str, str] = {}

    def read(path: str) -> str:
        if path not in cache:
            cache[path] = (root / path).read_text(encoding="utf-8", errors="replace")
        return cache[path]

    return read


def drop_prose_quotations(violations: list, read_source) -> list:
    """C1 のうち、名前が散文の引用としてのみ現れるものを落とす（他の検定は素通し）。"""
    return [
        v for v in violations
        if not (v.check == "C1" and is_prose_quotation(read_source(v.path), v.key))
    ]


def names_an_existing_file(root: Path, token: str) -> bool:
    """パス形の名指しについて、実在を**ファイルシステム**に問う（走査索引に問わない）。

    C1 のパス分岐は実在を `idx.files`（＝走査対象の索引）で代理していた。走査範囲の除外は
    「そこの違反を探さない」ためのものであり（`quality_scope.py` が理由を持つ）、「ディスク上に
    在るか」という別の問いへ流用してはならない。流用の結果、除外ディレクトリの**実在**ファイルを
    名指すだけで「存在しない」と報告された（2026-09-25 実測: 本ゲートの検定がゲート自身の実体を
    名指した場面）。これは散文の引用とコード参照の混同と同型の誤り——別々の問いを 1 つの代理変数で
    測っている——なので、同じ向きで直す: **問いごとに出所を分ける**。

    root の外へ出る名指し（``..`` を含むもの）は「在る」と答えない（判定を木の中に閉じる）。
    """
    candidate = root / token.split(":")[0]
    return candidate.is_file() and root.resolve() in candidate.resolve().parents


def drop_false_path_reports(violations: list, root: Path) -> list:
    """C1 のうち、名指されたパスが実在するのに「存在しない」と報せているものを落とす。"""
    return [
        v for v in violations
        if not (v.check == "C1" and di.PATHLIKE.match(v.key)
                and names_an_existing_file(root, v.key))
    ]


SUITES = {
    "declaration": (HERE / "di_baseline.json",
                    lambda: drop_false_path_reports(drop_prose_quotations(
                        di.run(REPO, di._infer_prefixes(REPO), {"C1", "C2", "C3"}),
                        source_reader(REPO)), REPO)),
    "test_quality": (HERE / "tq_baseline.json",
                     lambda: tq.run(REPO, set(tq.ALL_CHECKS))),
}


def _frozen(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="静的品質検定（Stop フック入口）")
    ap.add_argument("--write-baseline", action="store_true",
                    help="現在の違反を baseline へ凍結し直す")
    ap.add_argument("--prune-baseline", action="store_true",
                    help="解消済み違反だけを baseline から除去する。"
                         "凍結集合との積集合しか書かないため、追加（gate 弱体化）は構造的に不可能")
    ap.add_argument("--verbose", action="store_true", help="人間向けに全文を出す")
    a = ap.parse_args(argv)

    if a.prune_baseline:
        for name, (baseline, runner) in SUITES.items():
            frozen = _frozen(baseline)
            if not frozen:
                continue
            kept = sorted(frozen & {v.ident() for v in runner()})
            baseline.write_text(
                json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{name}: 解消済み {len(frozen) - len(kept)} 件を除去（凍結 {len(kept)} 件）")
        return 0

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
