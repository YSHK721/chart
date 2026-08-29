#!/usr/bin/env python3
"""テスト品質検定 — 実務で確立している静的テストスメル検出を AST で実装する。

`declaration_integrity.py`（C1-C3）と同じ契約で動く。対象コードを import しないため、
collection error が残る状態でも実行できる。ratchet（baseline 凍結）も同一。

検定一覧と、対応する既知の実務プラクティス:

  T1 WEAK   弱い / 恒真なアサーション。
            van Deursen et al. "Refactoring Test Code" (XP 2001) の test smell 群、
            および選言アサーション（片方が他方を包含する `a or b`）。
  T2 NOASRT アサーションを 1 つも持たないテスト関数（Assertionless Test）。
  T3 NONDET 非決定要素（sleep / 現在時刻 / 未シードの乱数 / uuid4）。
            Luo et al. "An Empirical Analysis of Flaky Tests" (FSE 2014) の
            flaky 原因上位 3 種（async-wait / concurrency / time）に対応。
  T4 SWALLW 例外の握り潰し（`except: pass` / `except: return False`）。
            条件付き skip のガードに置かれると、失敗が無言の skip に化ける。
  T5 SKIP   理由なし skip / 定数条件 skipif / module-level skip。
  T6 COND   テスト本体の分岐（Conditional Test Logic）。実行経路が入力で変わる。
  T7 MOCK   spec / autospec を持たないモック。
            実物に存在しないメソッドを持つ fake を許し、モック乖離を生む。
            CPython 公式 unittest.mock 文書が autospec の使用を推奨している。
  T8 STRUCT テストファイル basename 衝突と `sys.path` 改変。
            pytest の rootdir import で同名モジュールが衝突し、収集が壊れる。

使用:
    python test_quality.py <repo_root>
    python test_quality.py <repo_root> --write-baseline tq_baseline.json
    python test_quality.py <repo_root> --baseline tq_baseline.json --checks T1,T2,T7

抑止:
    違反行の末尾または直前行に `# tq-ok(T7): 理由` を置く。理由文字列は必須。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import warnings
from collections import defaultdict
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
SUPPRESS = re.compile(r"#\s*tq-ok\((T[1-8])\)\s*:\s*(\S.*)$")
ALL_CHECKS = ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")

ASSERT_CALLS = re.compile(r"^(assert|assert_|check_|expect)")
NONDET = {
    ("time", "sleep"): "sleep による待機",
    ("time", "time"): "現在時刻",
    ("time", "monotonic"): "現在時刻",
    ("datetime", "now"): "現在時刻",
    ("datetime", "today"): "現在時刻",
    ("datetime", "utcnow"): "現在時刻",
    ("date", "today"): "現在日",
    ("uuid", "uuid1"): "乱数由来の識別子",
    ("uuid", "uuid4"): "乱数由来の識別子",
}
RANDOM_FUNCS = {"random", "randint", "randrange", "choice", "choices",
                "shuffle", "sample", "uniform", "gauss", "normal", "rand", "randn"}
MOCK_FACTORIES = {"Mock", "MagicMock", "AsyncMock", "NonCallableMock"}
SPEC_KWARGS = {"spec", "spec_set", "autospec", "new", "new_callable"}


@dataclass(frozen=True)
class Violation:
    check: str
    path: str
    line: int
    key: str
    detail: str

    def ident(self) -> str:
        return f"{self.check}|{self.path}|{self.key}"


# ---------------------------------------------------------------- 収集


def _excluded(p: Path, root: Path) -> bool:
    return any(part in DEFAULT_EXCLUDE for part in p.relative_to(root).parts)


def collect_tests(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*.py"):
        if _excluded(p, root):
            continue
        rel = p.relative_to(root)
        # pytest 既定の python_files 規約に一致するものだけをテストとみなす。
        # tests/ 配下でも、テストデータ・ヘルパは対象外。
        if rel.name.startswith("test_") or rel.name.endswith("_test.py") or rel.name == "conftest.py":
            out.append(p)
    return out


def suppressions(src: str) -> dict[int, set[str]]:
    out: dict[int, set[str]] = defaultdict(set)
    for i, line in enumerate(src.splitlines(), start=1):
        m = SUPPRESS.search(line)
        if m:
            out[i].add(m.group(1))
            out[i + 1].add(m.group(1))
    return out


FIXTURE_DECOS = {"fixture", "yield_fixture"}


def _is_fixture(fn: ast.AST) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        nm = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if nm in FIXTURE_DECOS:
            return True
    return False


def test_funcs(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """モジュール直下の関数と Test* クラスのメソッドのみを対象にする。

    入れ子関数（ルートハンドラ・コールバック等）は名前が `test` で始まっても対象外。
    fixture も対象外。
    """
    out = []
    containers: list[ast.AST] = [tree]
    for n in ast.iter_child_nodes(tree):
        if isinstance(n, ast.ClassDef) and (n.name.startswith("Test") or n.name.endswith("Tests")):
            containers.append(n)
    for c in containers:
        for n in ast.iter_child_nodes(c):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name.startswith("test") and not _is_fixture(n):
                out.append(n)
    return out


def own_walk(fn: ast.AST):
    """入れ子関数 / lambda の内側に降りずに走査する。"""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def callee(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def dotted(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (node.value.id, node.attr)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
        return (node.value.attr, node.attr)
    return None


# ---------------------------------------------------------------- T1 / T2


TAUT_CMP = {">=": 0, ">": -1}


def _is_weak(test: ast.AST) -> str | None:
    if isinstance(test, ast.Constant):
        return "定数アサーション"
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return "選言アサーション（片方が成立すれば通る）"
    if isinstance(test, ast.Compare):
        # assert x is not None（単独）
        if len(test.ops) == 1 and isinstance(test.ops[0], ast.IsNot):
            if isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None:
                return "`is not None` のみ"
        # assert len(x) >= 0 / > -1
        if isinstance(test.left, ast.Call) and callee(test.left) == "len":
            for op, c in zip(test.ops, test.comparators):
                if isinstance(c, ast.Constant) and isinstance(c.value, int):
                    if isinstance(op, ast.GtE) and c.value <= 0:
                        return "`len(...) >= 0` は恒真"
                    if isinstance(op, ast.Gt) and c.value < 0:
                        return "`len(...) > 負数` は恒真"
        # assert a == a
        if len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
            if ast.dump(test.left) == ast.dump(test.comparators[0]):
                return "左右が同一式（恒真）"
    return None


def _assertions(fn: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    for n in own_walk(fn):
        if isinstance(n, ast.Assert):
            out.append(n)
        elif isinstance(n, ast.Call):
            name = callee(n)
            if ASSERT_CALLS.match(name) or name in {"raises", "warns", "fail"}:
                out.append(n)
        elif isinstance(n, ast.With):
            for item in n.items:
                if isinstance(item.context_expr, ast.Call) and callee(item.context_expr) in {"raises", "warns"}:
                    out.append(n)
    return out


def check_weak_and_missing(rel: Path, tree: ast.AST, checks: set[str]) -> list[Violation]:
    out: list[Violation] = []
    for fn in test_funcs(tree):
        asserts = _assertions(fn)
        if "T2" in checks and not asserts:
            out.append(Violation("T2", rel.as_posix(), fn.lineno, fn.name,
                                 "アサーションを 1 つも持たない"))
            continue
        if "T1" not in checks:
            continue
        hard = [a for a in asserts if isinstance(a, ast.Assert)]
        for a in hard:
            reason = _is_weak(a.test)
            if reason:
                # 単独の `is not None` のみ弱いと判定する（補助検査としてなら妥当）
                if reason == "`is not None` のみ" and len(asserts) > 1:
                    continue
                out.append(Violation("T1", rel.as_posix(), a.lineno, f"{fn.name}:L{a.lineno}", reason))
        for a in asserts:
            if isinstance(a, ast.Call) and callee(a) in {"assertIsNotNone", "assertTrue"} and len(asserts) == 1:
                out.append(Violation("T1", rel.as_posix(), a.lineno, f"{fn.name}:L{a.lineno}",
                                     f"`{callee(a)}` 単独では検査が弱い"))
    return out


# ---------------------------------------------------------------- T3


def check_nondeterminism(rel: Path, tree: ast.AST, src: str) -> list[Violation]:
    seeded = ("seed(" in src) or ("freeze_time" in src) or ("frozen" in src)
    out: list[Violation] = []
    for fn in test_funcs(tree):
        for n in own_walk(fn):
            if not isinstance(n, ast.Call):
                continue
            d = dotted(n.func)
            if d and d in NONDET:
                out.append(Violation("T3", rel.as_posix(), n.lineno, f"{fn.name}:{d[0]}.{d[1]}",
                                     NONDET[d]))
            name = callee(n)
            if name in RANDOM_FUNCS and not seeded:
                mod = d[0] if d else ""
                if mod in {"random", "np", "numpy", "rng", "default_rng"} or mod == "":
                    out.append(Violation("T3", rel.as_posix(), n.lineno, f"{fn.name}:{name}",
                                         "未シードの乱数"))
    return out


# ---------------------------------------------------------------- T4


def check_swallow(rel: Path, tree: ast.AST) -> list[Violation]:
    out: list[Violation] = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.ExceptHandler):
            continue
        body = n.body
        bare = n.type is None
        trivial = len(body) == 1 and (
            isinstance(body[0], ast.Pass)
            or (isinstance(body[0], ast.Return)
                and (body[0].value is None or isinstance(body[0].value, ast.Constant)))
        )
        if bare or trivial:
            kind = "bare except" if bare else "例外を握り潰して定数を返す"
            out.append(Violation("T4", rel.as_posix(), n.lineno, f"L{n.lineno}", kind))
    return out


# ---------------------------------------------------------------- T5


def check_skip(rel: Path, tree: ast.AST) -> list[Violation]:
    out: list[Violation] = []
    for fn in test_funcs(tree) + [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for dec in getattr(fn, "decorator_list", []):
            call = dec if isinstance(dec, ast.Call) else None
            target = call.func if call else dec
            d = dotted(target)
            nm = d[1] if d else getattr(target, "id", "")
            if nm == "skip":
                has_reason = bool(call and (call.args or any(k.arg == "reason" for k in call.keywords)))
                if not has_reason:
                    out.append(Violation("T5", rel.as_posix(), dec.lineno, f"{fn.name}:skip",
                                         "理由のない skip"))
            elif nm == "skipif" and call and call.args:
                cond = call.args[0]
                if isinstance(cond, ast.Constant) and bool(cond.value):
                    out.append(Violation("T5", rel.as_posix(), dec.lineno, f"{fn.name}:skipif",
                                         "定数条件の skipif（常に skip）"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and callee(n) == "skip":
            if any(k.arg == "allow_module_level" for k in n.keywords):
                out.append(Violation("T5", rel.as_posix(), n.lineno, f"module:L{n.lineno}",
                                     "モジュール全体の skip"))
    return out


# ---------------------------------------------------------------- T6


def check_conditional(rel: Path, tree: ast.AST) -> list[Violation]:
    out: list[Violation] = []
    for fn in test_funcs(tree):
        for n in own_walk(fn):
            if isinstance(n, (ast.If, ast.While)):
                if isinstance(n, ast.If) and isinstance(n.test, ast.Constant):
                    continue
                out.append(Violation("T6", rel.as_posix(), n.lineno, f"{fn.name}:L{n.lineno}",
                                     "テスト本体の分岐。実行経路が入力で変わる"))
                break
    return out


# ---------------------------------------------------------------- T7


def check_mock_spec(rel: Path, tree: ast.AST) -> list[Violation]:
    out: list[Violation] = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = callee(n)
        kw = {k.arg for k in n.keywords if k.arg}
        if name in MOCK_FACTORIES and not (kw & SPEC_KWARGS):
            out.append(Violation("T7", rel.as_posix(), n.lineno, f"{name}:L{n.lineno}",
                                 f"`{name}` が spec を持たない。実物に無い属性を許す"))
        elif name in {"patch", "object"} and _is_patch(n):
            # 位置引数で置換対象（new）を明示している場合は自動 Mock ではない
            explicit_new = len(n.args) >= (3 if name == "object" else 2)
            if not (kw & SPEC_KWARGS) and not explicit_new:
                out.append(Violation("T7", rel.as_posix(), n.lineno, f"patch:L{n.lineno}",
                                     "`patch` が autospec / new_callable を持たない"))
    return out


def _is_patch(node: ast.Call) -> bool:
    f = node.func
    if isinstance(f, ast.Name) and f.id == "patch":
        return True
    if isinstance(f, ast.Attribute):
        if f.attr == "patch":
            return True
        if f.attr == "object" and isinstance(f.value, ast.Name) and f.value.id == "patch":
            return True
    return False


# ---------------------------------------------------------------- T8


def check_structure(root: Path, files: list[Path]) -> list[Violation]:
    out: list[Violation] = []
    by_base: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        rel = f.relative_to(root)
        # conftest.py は pytest が特別扱いするため basename 衝突の対象外
        if rel.name in {"__init__.py", "conftest.py"}:
            continue
        pkg = f.parent / "__init__.py"
        by_base[rel.name].append(rel if pkg.exists() else rel)
        if not pkg.exists():
            by_base.setdefault("__nopkg__:" + rel.name, []).append(rel)
    for base, paths in sorted(by_base.items()):
        if base.startswith("__nopkg__:") or len(paths) < 2:
            continue
        nopkg = [p for p in paths if not (root / p).parent.joinpath("__init__.py").exists()]
        if len(nopkg) >= 2:
            out.append(Violation("T8", nopkg[0].as_posix(), 1, f"basename:{base}",
                                 f"`__init__.py` の無いディレクトリで basename が {len(nopkg)} 重複。"
                                 f"pytest の import 衝突を起こす: "
                                 + ", ".join(p.as_posix() for p in sorted(nopkg)[:4])))
    for f in files:
        rel = f.relative_to(root)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                v = n.func.value
                if isinstance(v, ast.Attribute) and v.attr == "path" \
                        and isinstance(v.value, ast.Name) and v.value.id == "sys" \
                        and n.func.attr in {"insert", "append"}:
                    out.append(Violation("T8", rel.as_posix(), n.lineno, f"syspath:L{n.lineno}",
                                         "テストが `sys.path` を改変する。"
                                         "モジュール同一性がプロダクトと食い違う"))
    return out


# ---------------------------------------------------------------- 実行


def run(root: Path, checks: set[str]) -> list[Violation]:
    files = collect_tests(root)
    out: list[Violation] = []
    for f in files:
        rel = f.relative_to(root)
        src = f.read_text(encoding="utf-8", errors="replace")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(src)
        except SyntaxError:
            continue
        sup = suppressions(src)
        found: list[Violation] = []
        found += check_weak_and_missing(rel, tree, checks)
        if "T3" in checks:
            found += check_nondeterminism(rel, tree, src)
        if "T4" in checks:
            found += check_swallow(rel, tree)
        if "T5" in checks:
            found += check_skip(rel, tree)
        if "T6" in checks:
            found += check_conditional(rel, tree)
        if "T7" in checks:
            found += check_mock_spec(rel, tree)
        for v in found:
            if v.check in checks and v.check not in sup.get(v.line, set()):
                out.append(v)
    if "T8" in checks:
        out += check_structure(root, files)
    return sorted(out, key=lambda v: (v.check, v.path, v.line))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="テスト品質検定")
    ap.add_argument("root", type=Path)
    ap.add_argument("--checks", default=",".join(ALL_CHECKS))
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--write-baseline", type=Path)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--exclude", action="append", default=[],
                    help="走査から外すディレクトリ名（複数可）。vendored な第三者コードなど")
    a = ap.parse_args(argv)

    DEFAULT_EXCLUDE.update(a.exclude)

    root = a.root.resolve()
    checks = set(a.checks.split(","))
    vs = run(root, checks)

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
    stale = frozen - {v.ident() for v in vs}

    if a.format == "json":
        print(json.dumps({"total": len(vs), "new": [asdict(v) for v in new],
                          "stale": sorted(stale)}, ensure_ascii=False, indent=1))
        return 1 if new else 0

    by: dict[str, int] = {}
    for v in vs:
        by[v.check] = by.get(v.check, 0) + 1
    print(f"検出 {len(vs)} 件  {dict(sorted(by.items()))}  "
          f"（凍結 {len(frozen)} / 新規 {len(new)} / 解消 {len(stale)}）")
    for v in new[: a.limit]:
        print(f"  {v.check} {v.path}:{v.line}  {v.key}  — {v.detail}")
    if len(new) > a.limit:
        print(f"  … 他 {len(new) - a.limit} 件")
    if stale:
        print(f"  解消済み {len(stale)} 件。baseline を更新する。")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
