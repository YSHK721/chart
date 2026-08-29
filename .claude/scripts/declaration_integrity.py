#!/usr/bin/env python3
"""宣言整合性検定 — テストとコードの乖離のうち「決定可能な部分」のみを機械検出する。

本ツールが検定するのは 3 つの構文的性質だけである。いずれも有限のグラフ・木の上の
判定問題であり、偽陰性を持たない。意味的性質（コードが仕様に従うか）は Rice の定理により
決定不能であるため、本ツールは一切扱わない。

  C1 DECL  コメント / docstring がバッククォートで名指す記号が、実在し、かつ
           そのモジュールから到達可能（import 済み or ローカル定義）であること。
  C2 GREP  テストが被検査コードのソース文字列に対して assertion を行っていないこと。
  C3 TAUT  等値 assertion の両辺が同一の被検査モジュール由来の呼び出しでないこと。

本ツールはコードを import しない。AST のみを読む。したがって collection error の
影響を受けず、テストスイートが 1 コマンドで回らない状態でも実行できる。

使用:
    python declaration_integrity.py <repo_root>
    python declaration_integrity.py <repo_root> --write-baseline baseline.json
    python declaration_integrity.py <repo_root> --baseline baseline.json
    python declaration_integrity.py <repo_root> --format json

抑止:
    違反行の末尾または直前行に `# di-ok(C3): 理由` を置く。理由文字列は必須。
"""

from __future__ import annotations

import argparse
import ast
import builtins
import io
import json
import keyword
import re
import sys
import tokenize
from dataclasses import dataclass, asdict
from pathlib import Path

DEFAULT_EXCLUDE = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", "build", "dist", ".tox",
    # 本チェックアウト以外の複製を走査しない。`.claude/worktrees` には作業ブランチの
    # 完全な複製が並ぶため（実測 2026-08-29: py 27,834 本のうち 24,226 本＝87%）、
    # 除外しないと他ブランチの違反まで拾い、走査も 7.7 倍になる。
    ".claude",
}

BACKTICK = re.compile(r"`([^`\n]{1,120})`")
DOTTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
PATHLIKE = re.compile(r"^[\w./\-]+\.(py|js|mjs|sh|md|json|csv|yml|yaml)(:[\d\-,]+)?$")
SUPPRESS = re.compile(r"#\s*di-ok\((C[123])\)\s*:\s*(\S.*)$")

BUILTIN_NAMES = set(dir(builtins)) | set(keyword.kwlist)
NOISE = {
    "self", "cls", "None", "True", "False", "args", "kwargs",
    "int", "str", "float", "bool", "list", "dict", "set", "tuple",
}

SOURCE_READ_FUNCS = {"read_text", "getsource", "getsourcelines", "read"}


@dataclass(frozen=True)
class Violation:
    check: str
    path: str
    line: int
    key: str
    detail: str

    def ident(self) -> str:
        return f"{self.check}|{self.path}|{self.key}"


# ---------------------------------------------------------------- 索引構築


class SymbolIndex:
    """リポジトリ全体の定義記号とモジュール名の索引。"""

    def __init__(self) -> None:
        self.defs: dict[str, set[str]] = {}
        self.modules: set[str] = set()
        self.files: set[str] = set()

    def add_module(self, rel: Path) -> None:
        self.files.add(rel.as_posix())
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            self.modules.add(".".join(parts))
            self.modules.add(parts[-1])

    def add_def(self, name: str, rel: Path) -> None:
        self.defs.setdefault(name, set()).add(rel.as_posix())

    def knows(self, name: str) -> bool:
        return name in self.defs or name in self.modules


def index_repo(root: Path, py_files: list[Path]) -> SymbolIndex:
    idx = SymbolIndex()
    for f in py_files:
        rel = f.relative_to(root)
        idx.add_module(rel)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                idx.add_def(node.name, rel)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        idx.add_def(tgt.id, rel)
    # JS 側は AST を持たないため、ファイル名のみ索引する（C1 のパス実在検定用）
    for f in root.rglob("*.js"):
        if not _excluded(f, root):
            idx.files.add(f.relative_to(root).as_posix())
    return idx


def _excluded(p: Path, root: Path) -> bool:
    return any(part in DEFAULT_EXCLUDE for part in p.relative_to(root).parts)


def collect_py(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if not _excluded(p, root)]


# ---------------------------------------------------------------- 補助


def bound_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """このモジュールで束縛される名前と、import されたモジュールパスを返す。"""
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                modules.add(a.name)
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names, modules


def suppressions(src: str) -> dict[int, set[str]]:
    """行番号 -> 抑止された検定種別。直前行の抑止も同一行に写像する。"""
    out: dict[int, set[str]] = {}
    for i, line in enumerate(src.splitlines(), start=1):
        m = SUPPRESS.search(line)
        if m:
            out.setdefault(i, set()).add(m.group(1))
            out.setdefault(i + 1, set()).add(m.group(1))
    return out


def comment_blocks(src: str) -> list[tuple[int, str]]:
    """`#` コメントを (行番号, 本文) で返す。"""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def docstring_blocks(tree: ast.AST) -> list[tuple[int, str]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ds = ast.get_docstring(node)
            if ds:
                line = getattr(node, "lineno", 1)
                out.append((line, ds))
    return out


# ---------------------------------------------------------------- C1


def _scope_names(node: ast.AST) -> set[str]:
    """関数 / クラスの局所束縛（引数・代入・入れ子定義）を集める。"""
    names: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
            names.add(arg.arg)
        if a.vararg:
            names.add(a.vararg.arg)
        if a.kwarg:
            names.add(a.kwarg.arg)
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


def _scope_map(tree: ast.AST) -> list[tuple[int, int, set[str]]]:
    """(開始行, 終了行, 局所名) を内側優先で返す。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(n, "end_lineno", n.lineno)
            out.append((n.lineno, end, _scope_names(n)))
    out.sort(key=lambda t: t[1] - t[0])
    return out


def check_declarations(rel: Path, tree: ast.AST, src: str, idx: SymbolIndex,
                       strict_unknown: bool = False) -> list[Violation]:
    names, modules = bound_names(tree)
    names |= set(rel.with_suffix("").parts)          # 自モジュール名の自己言及
    names |= _scope_names(tree)                       # モジュール直下の束縛
    scopes = _scope_map(tree)
    blocks = docstring_blocks(tree) + comment_blocks(src)
    out: list[Violation] = []
    seen: set[str] = set()

    def local_at(line: int) -> set[str]:
        acc: set[str] = set()
        for lo, hi, ns in scopes:
            if lo <= line <= hi:
                acc |= ns
        return acc

    for line, text in blocks:
        names_here = names | local_at(line)
        for token in BACKTICK.findall(text):
            token = token.strip()
            if token in seen or not token:
                continue
            if PATHLIKE.match(token):
                target = token.split(":")[0]
                if target not in idx.files and not any(
                    f.endswith("/" + target) for f in idx.files
                ):
                    seen.add(token)
                    out.append(Violation("C1", rel.as_posix(), line, token,
                                         "名指されたファイルが存在しない"))
                continue
            if not DOTTED.match(token):
                continue
            head = token.split(".")[0]
            tail = token.split(".")[-1]
            if tail in BUILTIN_NAMES or tail in NOISE or head in NOISE:
                continue
            if not idx.knows(tail) and not idx.knows(token):
                # プロジェクト外の語。既定では判定しない（英語散文の誤検出を避けるため）。
                if strict_unknown and ("_" in token or "." in token):
                    seen.add(token)
                    out.append(Violation("C1", rel.as_posix(), line, token,
                                         "名指された記号がリポジトリに存在しない"))
                continue
            reachable = (
                head in names_here
                or tail in names_here
                or token in modules
                or any(m == token or m.endswith("." + token) for m in modules)
                or any(m.startswith(token + ".") for m in modules)
            )
            if not reachable:
                seen.add(token)
                out.append(Violation("C1", rel.as_posix(), line, token,
                                     "実在するが、このモジュールから到達不能（未 import）"))
    return out


# ---------------------------------------------------------------- C2


class SourceReadTracker(ast.NodeVisitor):
    """ソース文字列を保持する変数名を収集する。"""

    def __init__(self) -> None:
        self.tainted: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._is_source_read(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.tainted.add(tgt.id)
        self.generic_visit(node)

    @staticmethod
    def _is_source_read(value: ast.AST) -> bool:
        for n in ast.walk(value):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in SOURCE_READ_FUNCS:
                    return True
        return False


def check_source_grep(rel: Path, tree: ast.AST) -> list[Violation]:
    tracker = SourceReadTracker()
    tracker.visit(tree)
    if not tracker.tainted:
        return []
    out: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        for n in ast.walk(node.test):
            hit = None
            if isinstance(n, ast.Compare):
                for op, cmp in zip(n.ops, n.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)) and _names(cmp) & tracker.tainted:
                        hit = _first(_names(cmp) & tracker.tainted)
            elif isinstance(n, ast.Call):
                fn = n.func
                nm = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if nm in {"search", "findall", "match", "count", "index", "finditer"}:
                    if _names(n) & tracker.tainted:
                        hit = _first(_names(n) & tracker.tainted)
            if hit:
                out.append(Violation("C2", rel.as_posix(), node.lineno, f"L{node.lineno}",
                                     f"被検査ソース文字列 `{hit}` に対する assertion"))
                break
    return out


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _first(s: set[str]) -> str:
    return sorted(s)[0]


# ---------------------------------------------------------------- C3


def check_tautology(rel: Path, tree: ast.AST, sut_prefixes: tuple[str, ...]) -> list[Violation]:
    """等値 assertion の両辺が被検査モジュール由来の呼び出しである場合を検出する。"""
    sut_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(sut_prefixes):
                sut_names |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(sut_prefixes):
                    sut_names.add((a.asname or a.name).split(".")[0])
    if not sut_names:
        return []

    out: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            test = node.test
        elif isinstance(node, ast.Call) and _callee(node) in {"assertEqual", "assert_allclose",
                                                              "assert_array_equal", "assert_frame_equal"}:
            if len(node.args) >= 2:
                if _calls_sut(node.args[0], sut_names) and _calls_sut(node.args[1], sut_names):
                    out.append(Violation("C3", rel.as_posix(), node.lineno, f"L{node.lineno}",
                                         "期待値と実測値の双方が被検査モジュールの呼び出し"))
            continue
        else:
            continue
        for n in ast.walk(test):
            if isinstance(n, ast.Compare) and any(isinstance(o, ast.Eq) for o in n.ops):
                left_ok = _calls_sut(n.left, sut_names)
                right_ok = any(_calls_sut(c, sut_names) for c in n.comparators)
                if left_ok and right_ok:
                    out.append(Violation("C3", rel.as_posix(), node.lineno, f"L{node.lineno}",
                                         "期待値と実測値の双方が被検査モジュールの呼び出し"))
                    break
    return out


def _callee(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _calls_sut(node: ast.AST, sut_names: set[str]) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in sut_names:
                return True
            if isinstance(f, ast.Attribute):
                base = f.value
                if isinstance(base, ast.Name) and base.id in sut_names:
                    return True
    return False


# ---------------------------------------------------------------- 実行


def is_test_file(rel: Path) -> bool:
    return rel.name.startswith("test_") or rel.name.endswith("_test.py") or "tests" in rel.parts


def run(root: Path, sut_prefixes: tuple[str, ...], checks: set[str],
        strict_unknown: bool = False) -> list[Violation]:
    py = collect_py(root)
    idx = index_repo(root, py)
    out: list[Violation] = []
    for f in py:
        rel = f.relative_to(root)
        src = f.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        sup = suppressions(src)
        found: list[Violation] = []
        if "C1" in checks:
            found += check_declarations(rel, tree, src, idx, strict_unknown)
        if is_test_file(rel):
            if "C2" in checks:
                found += check_source_grep(rel, tree)
            if "C3" in checks:
                found += check_tautology(rel, tree, sut_prefixes)
        for v in found:
            if v.check in sup.get(v.line, set()):
                continue
            out.append(v)
    return sorted(out, key=lambda v: (v.check, v.path, v.line))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="宣言整合性検定")
    ap.add_argument("root", type=Path)
    ap.add_argument("--sut-prefix", action="append", default=[],
                    help="被検査パッケージの接頭辞（C3 用・複数可）")
    ap.add_argument("--checks", default="C1,C2,C3")
    ap.add_argument("--strict-unknown", action="store_true",
                    help="リポジトリに存在しない記号の名指しも違反にする（誤検出増）")
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--write-baseline", type=Path)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--exclude", action="append", default=[],
                    help="走査から外すディレクトリ名（複数可）。vendored な第三者コードなど")
    a = ap.parse_args(argv)

    DEFAULT_EXCLUDE.update(a.exclude)
    root = a.root.resolve()
    prefixes = tuple(a.sut_prefix) if a.sut_prefix else _infer_prefixes(root)
    checks = set(a.checks.split(","))
    vs = run(root, prefixes, checks, a.strict_unknown)

    if a.write_baseline:
        a.write_baseline.write_text(
            json.dumps(sorted(v.ident() for v in vs), ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"baseline: {len(vs)} 件を凍結 -> {a.write_baseline}")
        return 0

    frozen: set[str] = set()
    if a.baseline and a.baseline.exists():
        frozen = set(json.loads(a.baseline.read_text(encoding="utf-8")))
    new = [v for v in vs if v.ident() not in frozen]
    fixed = frozen - {v.ident() for v in vs}

    if a.format == "json":
        print(json.dumps({"total": len(vs), "new": [asdict(v) for v in new],
                          "fixed": sorted(fixed)}, ensure_ascii=False, indent=1))
    else:
        by = {}
        for v in vs:
            by[v.check] = by.get(v.check, 0) + 1
        print(f"検出 {len(vs)} 件  {by}  （凍結 {len(frozen)} / 新規 {len(new)} / 解消 {len(fixed)}）")
        for v in new:
            print(f"  {v.check} {v.path}:{v.line}  {v.key}  — {v.detail}")
        if fixed:
            print(f"  解消済み {len(fixed)} 件。baseline を更新する。")
    return 1 if new else 0


def _infer_prefixes(root: Path) -> tuple[str, ...]:
    out = []
    for p in root.iterdir():
        if p.is_dir() and not p.name.startswith(".") and p.name not in DEFAULT_EXCLUDE:
            if any(p.rglob("*.py")):
                out.append(p.name)
    return tuple(out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
