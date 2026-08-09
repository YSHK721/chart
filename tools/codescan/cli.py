"""codescan のコマンドライン入口。

使い方（既定）:
    python -m tools.codescan

    → .codescan/rows.csv    全行の台帳（原子ステップ = 1 行）
      .codescan/report.json 重複クラスタ・依存グラフ・種別内訳
      標準出力              要約

重複を 1 件ずつ潰す運用:
    python -m tools.codescan --only-dup line --min-tok 4 --sort dup
    → 完全一致で 2 回以上出る行だけを、重複数の多い順に並べて出す。
      同じ ``line_group`` の行が 1 つの重複塊である。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import default_registry
from .collector import Scope, collect, iter_files
from .dependencies import Resolver, build_graph, python_roots
from .duplication import cluster_fragments, diverged_names, drop_blocks_inside, find_block_clones
from .javascript_analyzer import LIMITATIONS as JS_LIMITATIONS
from .report import build_report, resolve_destination, write_csv, write_json, write_summary
from .rows import DUP_FILTERS, assign_numbers, build_rows, filter_rows, sort_rows, summarize_kinds, type_symbols


def find_repo_root(start: Path) -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                             capture_output=True, text=True, check=True)
        return Path(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return start


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.codescan",
        description="コード重複・依存関係・シンボル種別を 1 行単位の台帳で出す。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="*", help="走査するディレクトリ・ファイル（既定: リポジトリ全体）")
    parser.add_argument("--repo-root", default=None, help="リポジトリ根（既定: git 管理の根）")
    parser.add_argument("--include", action="append", default=[], metavar="GLOB",
                        help="走査対象へ追加（台帳 tools/codescan_scope.txt の後ろに追記される）")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                        help="走査対象から除外（同上）")

    group = parser.add_argument_group("重複判定")
    group.add_argument("--min-tokens", type=int, default=40,
                       help="宣言単位クローンの最小トークン数（既定 40）")
    group.add_argument("--min-lines", type=int, default=5,
                       help="宣言単位クローンの最小行数（既定 5）")
    group.add_argument("--window", type=int, default=60,
                       help="ブロック単位クローンの種となる連続トークン数（既定 60）")
    group.add_argument("--max-occurrences", type=int, default=40,
                       help="同一窓がこの回数を超えて出るなら種にしない（既定 40・除外数は要約に出す）")
    group.add_argument("--no-blocks", action="store_true", help="ブロック単位クローンの走査を行わない")

    group = parser.add_argument_group("行台帳の抽出")
    group.add_argument("--sort", choices=["path", "code", "shape", "dup"], default="code",
                       help="出力順（既定 code = code_key 順。完全一致の行が隣接する）")
    group.add_argument("--only-dup", choices=list(DUP_FILTERS), default="none",
                       help="; ".join(f"{k}={v}" for k, v in DUP_FILTERS.items()))
    group.add_argument("--min-tok", type=int, default=0,
                       help="この数未満のトークンしかない行を落とす（`}` 等の定型行対策）")
    group.add_argument("--skip-kinds", default="", metavar="KIND[,KIND]",
                       help="指定した kind の行を落とす（例: import,comment）。既定は落とさない")
    group.add_argument("--offset", type=int, default=0, help="先頭から読み飛ばす行数")
    group.add_argument("--limit", type=int, default=0, help="出力する行数の上限（0 = 無制限）")

    group = parser.add_argument_group("出力")
    group.add_argument("--out", default=".codescan", help="出力ディレクトリ（既定 .codescan）")
    group.add_argument("--csv", default=None, help="行台帳の出力先（`-` で標準出力）")
    group.add_argument("--json", default=None, help="全体報告の出力先（`-` で標準出力）")
    group.add_argument("--no-summary", action="store_true", help="要約を出さない")
    group.add_argument("--fail-over", type=int, default=None, metavar="N",
                       help="単一ソース化で消える見込み行数が N を超えたら終了コード 1")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(Path.cwd())
    registry = default_registry()
    scope = Scope.from_ledger(repo_root, args.include, args.exclude)

    roots = [str(Path(p).resolve().relative_to(repo_root)) for p in args.paths] if args.paths else []
    paths, aliases = iter_files(repo_root, scope, registry, roots)
    if not paths:
        print("走査対象が 0 件。--include / tools/codescan_scope.txt を確認せよ。", file=sys.stderr)
        return 2
    modules, sources = collect(repo_root, paths, registry)

    fragments = [f for m in modules for f in m.fragments]
    function_clones = cluster_fragments(fragments, args.min_tokens, args.min_lines)
    if args.no_blocks:
        block_clones, block_stats = [], {"skipped": "--no-blocks 指定のため未走査"}
    else:
        block_clones, block_stats = find_block_clones(
            modules, args.window, max(args.window, args.min_tokens), args.max_occurrences)
        block_clones = drop_blocks_inside(block_clones, function_clones)

    resolver = Resolver(repo_root, set(paths), python_roots(repo_root))
    graph = build_graph(modules, resolver)

    rows = build_rows(modules, sources, function_clones, block_clones, repo_root)
    report = build_report(
        scope_rules=scope.rules, aliases=aliases, modules=modules, function_clones=function_clones,
        block_clones=block_clones, diverged=diverged_names(modules, args.min_tokens),
        graph=graph, kind_counts=summarize_kinds(modules), types=type_symbols(modules),
        block_stats=block_stats, limitations=JS_LIMITATIONS, rows=rows,
    )

    skip_kinds = frozenset(k.strip() for k in args.skip_kinds.split(",") if k.strip())
    selected = filter_rows(rows, args.only_dup, args.min_tok, skip_kinds)
    selected = assign_numbers(sort_rows(selected, args.sort))
    if args.offset or args.limit:
        end = args.offset + args.limit if args.limit else len(selected)
        selected = selected[args.offset:end]

    out_dir = Path(args.out)
    csv_target = resolve_destination(args.csv if args.csv is not None else str(out_dir / "rows.csv"))
    if csv_target is None:
        write_csv(selected, sys.stdout)
    else:
        with csv_target.open("w", encoding="utf-8", newline="") as handle:
            write_csv(selected, handle)

    json_target = resolve_destination(args.json if args.json is not None else str(out_dir / "report.json"))
    if json_target is None:
        write_json(report, sys.stdout)
    else:
        with json_target.open("w", encoding="utf-8") as handle:
            write_json(report, handle)

    if not args.no_summary:
        write_summary(report, selected, sys.stdout)
        if csv_target is not None:
            print(f"\n[出力] 行台帳: {csv_target}  （{len(selected)} 行）")
        if json_target is not None:
            print(f"[出力] 全体報告: {json_target}")

    if args.fail_over is not None and report["duplication"]["removable_lines_total"] > args.fail_over:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
