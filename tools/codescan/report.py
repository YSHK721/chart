"""出力（CSV / JSON / テキスト要約）。

行台帳の列は ``rows.COLUMNS`` が唯一源。ここに列名を書き写さない。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from .model import Clone
from .rows import COLUMNS


def write_csv(rows: "list[dict]", destination) -> None:
    """行台帳を CSV で書く。表計算で ``code_key`` 列ソートする運用を前提とする。"""
    writer = csv.DictWriter(destination, fieldnames=list(COLUMNS), extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)


def _clone_dict(clone: Clone) -> dict:
    return {
        "clone_type": clone.clone_type,
        "unit": clone.unit,
        "tokens": clone.token_count,
        "occurrences": [
            {"path": o.path, "name": o.name, "kind": o.kind,
             "start_line": o.start_line, "end_line": o.end_line, "lines": o.line_count}
            for o in clone.occurrences
        ],
        "removable_lines": clone.removable_lines,
        "cross_file": clone.cross_file,
    }


def build_report(*, scope_rules, modules, function_clones, block_clones, diverged,
                 graph, kind_counts, types, block_stats, limitations, rows) -> dict:
    """機械可読の全体報告を組む。"""
    errors = [{"path": m.path, "errors": list(m.errors)} for m in modules if m.errors]
    return {
        "scope": {"rules": scope_rules, "files": len(modules),
                  "loc": sum(m.loc for m in modules),
                  "languages": sorted({m.language for m in modules})},
        "symbols": {"by_kind": kind_counts, "types": types},
        "dependencies": {
            "internal_edges": [{"from": a, "to": b} for a, b in graph["edges"]],
            "external": graph["external"],
            "cycles": graph["cycles"],
            "unresolved": graph["unresolved"][:200],
            "unresolved_total": len(graph["unresolved"]),
            "fan_in_top": _top(graph["fan_in"]),
            "fan_out_top": _top(graph["fan_out"]),
        },
        "duplication": {
            "function_clones": [_clone_dict(c) for c in function_clones],
            "block_clones": [_clone_dict(c) for c in block_clones],
            "diverged_names": diverged,
            "block_scan": block_stats,
            "duplicated_lines": sum(1 for r in rows if r["line_dup"] >= 2),
            "shape_duplicated_lines": sum(1 for r in rows if r["shape_dup"] >= 2),
            "removable_lines_total": sum(c.removable_lines for c in function_clones)
                                     + sum(c.removable_lines for c in block_clones),
        },
        "limitations": list(limitations),
        "parse_errors": errors,
    }


def _top(counts: "dict[str, int]", limit: int = 20) -> "list[dict]":
    return [{"path": p, "count": n}
            for p, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def write_json(report: dict, destination) -> None:
    json.dump(report, destination, ensure_ascii=False, indent=2)
    destination.write("\n")


def write_summary(report: dict, rows: "list[dict]", destination=sys.stdout, top: int = 15) -> None:
    """人間が最初に読む要約。数字は必ず算出根拠（件数・行数）とともに出す。"""
    out = destination.write
    scope = report["scope"]
    dup = report["duplication"]
    out(f"[走査] {scope['files']} ファイル / {scope['loc']} 行 / {', '.join(scope['languages'])}\n")
    out(f"[行]   出力 {len(rows)} 行（原子ステップ = 1 行）\n")
    out(f"[重複] 完全一致で 2 回以上出る行: {dup['duplicated_lines']}"
        f" / 名前を無視した一致: {dup['shape_duplicated_lines']}\n")
    out(f"       宣言単位クローン {len(dup['function_clones'])} 件"
        f" / ブロック単位クローン {len(dup['block_clones'])} 件"
        f" / 同名別実装 {len(dup['diverged_names'])} 件\n")
    out(f"       単一ソース化で消える見込み行数: {dup['removable_lines_total']}\n")
    skipped = dup["block_scan"].get("skipped_boilerplate_windows", 0)
    if skipped:
        out(f"       ※ 出現が {dup['block_scan']['max_occurrences_threshold']} 回を超える定型窓"
            f" {skipped} 件は種にしていない（--max-occurrences で変更可）\n")

    out("\n[削減行数の大きい重複]\n")
    clones = sorted(dup["function_clones"] + dup["block_clones"],
                    key=lambda c: -c["removable_lines"])[:top]
    for index, clone in enumerate(clones, start=1):
        head = clone["occurrences"][0]
        out(f"  {index:2d}. -{clone['removable_lines']:4d} 行 "
            f"[{clone['clone_type']}/{clone['unit']}] {len(clone['occurrences'])} 箇所 "
            f"{head['name'] or head['path']}\n")
        for occurrence in clone["occurrences"][:4]:
            out(f"        {occurrence['path']}:{occurrence['start_line']}-{occurrence['end_line']}\n")
        if len(clone["occurrences"]) > 4:
            out(f"        … 他 {len(clone['occurrences']) - 4} 箇所\n")

    if dup["diverged_names"]:
        out("\n[同名別実装（複製が片方だけ直された疑い）]\n")
        for entry in dup["diverged_names"][:top]:
            structural = "構造差あり" if entry["shape_variants"] > 1 else "定数・名前のみ差"
            out(f"  {entry['name']}  {len(entry['occurrences'])} 箇所 / {entry['variants']} 種"
                f" / {structural}\n")
            for occurrence in entry["occurrences"][:4]:
                out(f"        {occurrence['path']}:{occurrence['line']}\n")

    deps = report["dependencies"]
    out(f"\n[依存] 内部辺 {len(deps['internal_edges'])} / 未解決 {deps['unresolved_total']} / "
        f"循環 {len(deps['cycles'])} 件\n")
    for cycle in deps["cycles"][:top]:
        out(f"  循環({len(cycle)}): " + " → ".join(cycle[:6]) + (" …" if len(cycle) > 6 else "") + "\n")

    out("\n[種別内訳]\n")
    for kind, count in list(report["symbols"]["by_kind"].items()):
        out(f"  {kind:<18} {count}\n")

    if report["parse_errors"]:
        out(f"\n[解析失敗] {len(report['parse_errors'])} ファイル\n")
        for entry in report["parse_errors"][:10]:
            out(f"  {entry['path']}: {entry['errors'][0]}\n")


def resolve_destination(path: "str | None"):
    if not path or path == "-":
        return None
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
