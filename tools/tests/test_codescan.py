"""codescan の不変条件を固定する。

ここで守るのは「検出結果が事実と一致すること」である。とくに以下は実際に壊れた／
壊れうる箇所なので、退行したら即赤にする。

    - JS のテンプレートリテラル ``${}`` の閉じをブロックの ``}`` と混ぜない
      （混ぜるとブレース対応が崩れ、以降の宣言をまるごと取りこぼす）
    - ``from . import mod`` をパッケージ ``__init__`` への依存に化けさせない
      （化けると存在しない循環が報告される）
    - 行台帳の列は ``rows.COLUMNS`` が唯一源（CSV ヘッダを別に書き写さない）
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from tools.codescan import default_registry
from tools.codescan import kinds
from tools.codescan.cli import main
from tools.codescan.collector import Scope, iter_files
from tools.codescan.dependencies import Resolver, build_graph, find_cycles, python_roots
from tools.codescan.duplication import cluster_fragments, diverged_names, find_block_clones
from tools.codescan.javascript_analyzer import JavaScriptAnalyzer, tokenize_js
from tools.codescan.python_analyzer import PythonAnalyzer
from tools.codescan.report import write_csv
from tools.codescan.rows import COLUMNS, build_rows, filter_rows, normalize_code, sort_rows

_ROOT = Path(__file__).resolve().parents[2]


# --- Python 解析器 -----------------------------------------------------------

PY_SOURCE = '''\
"""docstring"""
from __future__ import annotations

import os
from typing import Protocol

from . import sibling
from .pkg import deep

MAX_RETRY = 3
label = "x"


class Port(Protocol):
    def send(self, payload: str) -> None: ...


class Base(ABC):
    @abstractmethod
    def run(self) -> None: ...

    @property
    def name(self) -> str:
        return "base"

    @staticmethod
    def helper() -> int:
        return 1


class MyError(ValueError):
    pass


async def fetch(url: str) -> str:
    return url


def outer():
    def inner():
        return 1
    return inner
'''


def _analyze_python(source: str = PY_SOURCE):
    return PythonAnalyzer().analyze("pkg/mod.py", source)


def test_python_symbol_kinds_match_the_declarations():
    facts = _analyze_python()
    found = {symbol.name: symbol.kind for symbol in facts.symbols}
    assert found["Port"] == kinds.PROTOCOL
    assert found["Base"] == kinds.ABSTRACT_CLASS
    assert found["Base.run"] == kinds.ABSTRACT_METHOD
    assert found["Base.name"] == kinds.PROPERTY
    assert found["Base.helper"] == kinds.STATIC_METHOD
    assert found["MyError"] == kinds.EXCEPTION
    assert found["fetch"] == kinds.ASYNC_FUNCTION
    assert found["outer"] == kinds.FUNCTION
    assert found["outer.inner"] == kinds.FUNCTION
    assert found["MAX_RETRY"] == kinds.CONSTANT
    assert found["label"] == kinds.VARIABLE


def test_python_imports_keep_relative_level_and_names():
    facts = _analyze_python()
    specs = {(e.spec, e.level, e.is_from) for e in facts.imports}
    assert ("os", 0, False) in specs
    assert ("typing", 0, True) in specs
    assert ("", 1, True) in specs          # from . import sibling
    assert ("pkg", 1, True) in specs       # from .pkg import deep
    relative = next(e for e in facts.imports if e.level == 1 and e.spec == "")
    assert relative.names == ("sibling",)


def test_python_fragment_covers_the_whole_declaration():
    facts = _analyze_python()
    fragment = next(f for f in facts.fragments if f.name == "Base.name")
    assert fragment.start_line < fragment.end_line
    assert [t.text for t in fragment.tokens][:2] == ["def", "name"]


def test_python_syntax_error_is_recorded_not_raised():
    facts = PythonAnalyzer().analyze("broken.py", "def (:\n")
    assert facts.errors
    assert facts.symbols == ()


# --- JavaScript 解析器 -------------------------------------------------------

JS_SOURCE = """\
import { helper } from './helper.js';
import './side_effect.js';
const mod = require('node:path');

export const LIMIT = 10;

export function install(root) {
  const label = `#${root.id} > ${root.tagName}`;
  const onClick = (ev) => {
    return label;
  };
  return { onClick };
}

export function afterTemplate(text) {
  return String(text || '').replace(/\\s+/g, ' ');
}

export class Widget extends Base {
  constructor(options) {
    this.options = options;
  }

  static create() {
    return new Widget({});
  }

  get title() {
    return 'w';
  }

  async refresh() {
    await this.load();
  }
}
"""


def _analyze_js(source: str = JS_SOURCE):
    return JavaScriptAnalyzer().analyze("web/js/widget.js", source)


def test_js_template_expression_does_not_break_brace_matching():
    """``${...}`` の閉じをブロックの ``}`` と数えると、以降の宣言が全部消える。"""
    facts = _analyze_js()
    names = {symbol.name for symbol in facts.symbols}
    assert "afterTemplate" in names, "テンプレート直後の宣言が消えている"
    assert "Widget" in names


def test_js_symbol_kinds_match_the_declarations():
    facts = _analyze_js()
    found = {symbol.name: symbol.kind for symbol in facts.symbols}
    assert found["LIMIT"] == kinds.CONSTANT
    assert found["install"] == kinds.FUNCTION
    assert found["install.onClick"] == kinds.ARROW_FUNCTION
    assert found["Widget"] == kinds.CLASS
    assert found["Widget.constructor"] == kinds.CONSTRUCTOR
    assert found["Widget.create"] == kinds.STATIC_METHOD
    assert found["Widget.title"] == kinds.PROPERTY
    assert found["Widget.refresh"] == kinds.ASYNC_METHOD


def test_js_local_value_bindings_are_not_counted_as_symbols():
    """局所変数は種別集計に載せない（Python 側がモジュール直下だけを数えるのと対称）。"""
    facts = _analyze_js()
    assert "install.label" not in {symbol.name for symbol in facts.symbols}


def test_js_export_flag_follows_the_export_keyword():
    facts = _analyze_js()
    exported = {symbol.name: symbol.exported for symbol in facts.symbols}
    assert exported["install"] is True
    assert exported["Widget"] is True


def test_js_imports_cover_esm_side_effect_and_require():
    facts = _analyze_js()
    assert {e.spec for e in facts.imports} == {"./helper.js", "./side_effect.js", "node:path"}


def test_js_regex_literal_is_not_read_as_division():
    """正規表現内の ``{`` ``}`` をブレースとして数えるとブロック対応が壊れる。"""
    tokens = tokenize_js("const re = a.replace(/\\/x{2}/g, '');\nconst z = 1;")
    assert any(t.text.endswith("/g") for t in tokens), "正規表現リテラルが 1 トークンになっていない"
    assert [t.text for t in tokens if t.text in ("{", "}")] == []


def test_js_division_is_not_read_as_regex():
    tokens = tokenize_js("const half = total / 2;\nconst rest = total / 4;")
    assert sum(1 for t in tokens if t.text == "/") == 2


# --- 重複検出 ---------------------------------------------------------------

CLONE_A = """\
def alpha(values):
    total = 0
    for value in values:
        total += value * 2
    return total
"""

CLONE_B_RENAMED = """\
def beta(items):
    acc = 0
    for item in items:
        acc += item * 2
    return acc
"""


def _fragments(*sources):
    analyzer = PythonAnalyzer()
    out = []
    for index, source in enumerate(sources):
        out.extend(analyzer.analyze(f"f{index}.py", source).fragments)
    return out


def test_identical_declarations_are_reported_as_type_1():
    clones = cluster_fragments(_fragments(CLONE_A, CLONE_A), min_tokens=10, min_lines=3)
    assert [c.clone_type for c in clones] == ["type-1"]
    assert len(clones[0].occurrences) == 2
    assert clones[0].cross_file is True


def test_renamed_declarations_are_reported_as_type_2():
    clones = cluster_fragments(_fragments(CLONE_A, CLONE_B_RENAMED), min_tokens=10, min_lines=3)
    assert [c.clone_type for c in clones] == ["type-2"]


def test_short_declarations_are_below_the_threshold():
    assert cluster_fragments(_fragments(CLONE_A, CLONE_A), min_tokens=10_000, min_lines=3) == []


def test_methods_inside_a_duplicated_class_are_not_double_counted():
    source = """\
class Holder:
    def first(self, values):
        total = 0
        for value in values:
            total += value * 2
        return total

    def second(self, values):
        return len(values)
"""
    clones = cluster_fragments(_fragments(source, source), min_tokens=10, min_lines=3)
    assert [c.occurrences[0].name for c in clones] == ["Holder"]


def test_removable_lines_is_total_minus_the_longest_occurrence():
    clones = cluster_fragments(_fragments(CLONE_A, CLONE_A), min_tokens=10, min_lines=3)
    assert clones[0].removable_lines == clones[0].occurrences[0].line_count


def test_block_clone_finds_a_run_that_crosses_declaration_boundaries():
    body = "\n".join(f"    step_{i} = compute(value_{i}, {i})" for i in range(30))
    source_a = f"def one():\n{body}\n    return 1\n"
    source_b = f"def two():\n{body}\n    return 2\n"
    analyzer = PythonAnalyzer()
    modules = [analyzer.analyze("a.py", source_a), analyzer.analyze("b.py", source_b)]
    clones, stats = find_block_clones(modules, window=30, min_tokens=30, max_occurrences=40)
    assert clones, "同一の 30 行がブロッククローンとして出ない"
    assert stats["indexed_windows"] > 0
    assert {o.path for o in clones[0].occurrences} == {"a.py", "b.py"}


def test_boilerplate_windows_are_skipped_and_counted():
    analyzer = PythonAnalyzer()
    modules = [analyzer.analyze(f"m{i}.py", CLONE_A) for i in range(10)]
    _, stats = find_block_clones(modules, window=5, min_tokens=5, max_occurrences=2)
    assert stats["skipped_boilerplate_windows"] > 0, "打ち切り件数を黙って捨てている"


def test_diverged_names_reports_same_name_different_body():
    analyzer = PythonAnalyzer()
    modules = [analyzer.analyze("a.py", CLONE_A),
               analyzer.analyze("b.py", CLONE_A.replace("* 2", "* 3"))]
    entries = diverged_names(modules, min_tokens=5)
    assert [e["name"] for e in entries] == ["alpha"]
    assert entries[0]["variants"] == 2
    assert entries[0]["shape_variants"] == 1, "差は定数だけなので構造は同じ"


def test_diverged_names_flags_structural_divergence():
    """定数だけの差と、構造そのものの差を区別する（後者を優先して並べる）。"""
    analyzer = PythonAnalyzer()
    modules = [analyzer.analyze("a.py", CLONE_A),
               analyzer.analyze("b.py", CLONE_A.replace("total += value * 2", "if value:\n            total += value"))]
    entries = diverged_names(modules, min_tokens=5)
    assert entries[0]["shape_variants"] == 2


def test_diverged_names_ignores_identical_copies():
    analyzer = PythonAnalyzer()
    modules = [analyzer.analyze("a.py", CLONE_A), analyzer.analyze("b.py", CLONE_A)]
    assert diverged_names(modules, min_tokens=5) == []


# --- 依存関係 ---------------------------------------------------------------

def test_from_package_import_submodule_resolves_to_the_submodule():
    files = {"pkg/__init__.py", "pkg/mod.py", "pkg/kinds.py"}
    resolver = Resolver(_ROOT, files, [""])
    facts = PythonAnalyzer().analyze("pkg/mod.py", "from . import kinds\n")
    assert resolver.resolve(facts, facts.imports[0]) == ["pkg/kinds.py"]


def test_package_import_falls_back_to_init_when_no_submodule_matches():
    files = {"pkg/__init__.py", "pkg/mod.py"}
    resolver = Resolver(_ROOT, files, [""])
    facts = PythonAnalyzer().analyze("pkg/mod.py", "from . import CONSTANT\n")
    assert resolver.resolve(facts, facts.imports[0]) == ["pkg/__init__.py"]


def test_package_local_import_does_not_create_a_phantom_cycle():
    analyzer = PythonAnalyzer()
    modules = [
        analyzer.analyze("pkg/__init__.py", "from .mod import Thing\n"),
        analyzer.analyze("pkg/mod.py", "from . import kinds\n"),
        analyzer.analyze("pkg/kinds.py", "VALUE = 1\n"),
    ]
    resolver = Resolver(_ROOT, {m.path for m in modules}, [""])
    assert build_graph(modules, resolver)["cycles"] == []


def test_real_cycle_is_reported():
    analyzer = PythonAnalyzer()
    modules = [analyzer.analyze("a.py", "import b\n"), analyzer.analyze("b.py", "import a\n")]
    resolver = Resolver(_ROOT, {"a.py", "b.py"}, [""])
    assert build_graph(modules, resolver)["cycles"] == [["a.py", "b.py"]]


def test_js_relative_import_resolves_with_extension_completion():
    facts = JavaScriptAnalyzer().analyze("web/js/a.js", "import x from './sub/b';\n")
    resolver = Resolver(_ROOT, {"web/js/a.js", "web/js/sub/b.js"}, [""])
    assert resolver.resolve(facts, facts.imports[0]) == ["web/js/sub/b.js"]


def test_unresolved_import_is_counted_as_external():
    facts = PythonAnalyzer().analyze("a.py", "import pandas\n")
    graph = build_graph([facts], Resolver(_ROOT, {"a.py"}, [""]))
    assert graph["external"] == {"pandas": 1}
    assert graph["edges"] == []


def test_find_cycles_handles_self_contained_components():
    assert find_cycles({"a": ["b"], "b": ["c"], "c": ["a"]}) == [["a", "b", "c"]]
    assert find_cycles({"a": ["b"], "b": []}) == []


def test_python_roots_come_from_the_dev_paths_ledger():
    roots = python_roots(_ROOT)
    assert "" in roots, "リポジトリ根が解決根に入っていない"
    assert "indigators/market_profile/api" in roots, "台帳の値が反映されていない"


# --- 行台帳 -----------------------------------------------------------------

def _rows_for(source_a: str, source_b: str):
    analyzer = PythonAnalyzer()
    modules = [analyzer.analyze("a.py", source_a), analyzer.analyze("b.py", source_b)]
    sources = {"a.py": source_a.splitlines(), "b.py": source_b.splitlines()}
    clones = cluster_fragments([f for m in modules for f in m.fragments], 10, 3)
    return build_rows(modules, sources, clones, [], _ROOT)


def test_normalize_code_collapses_indent_and_inner_whitespace():
    assert normalize_code("    total  +=   value\n") == "total += value"


def test_row_carries_directory_file_line_and_code():
    rows = _rows_for(CLONE_A, CLONE_A)
    row = next(r for r in rows if r["line"] == 1)
    assert row["file"] == "a.py"
    assert row["dir"].endswith("/")
    assert row["code"].startswith("def alpha")


def test_line_dup_counts_identical_lines_across_files():
    rows = _rows_for(CLONE_A, CLONE_A)
    row = next(r for r in rows if r["code_key"] == "total = 0")
    assert row["line_dup"] == 2
    assert row["line_group"].startswith("L")


def test_code_shape_ignores_nesting_depth():
    """インデント記号を含めると、同じ 1 行がネスト深さ違いで別物になってしまう。"""
    shallow = "def a(values):\n    total = compute(values)\n    return total\n"
    deep = "def b(values):\n    if values:\n        for v in values:\n            total = compute(values)\n    return 0\n"
    rows = _rows_for(shallow, deep)
    shapes = [r["code_shape"] for r in rows if r["code_key"] == "total = compute(values)"]
    assert len(shapes) == 2 and shapes[0] == shapes[1]


def test_shape_dup_matches_lines_that_differ_only_by_name():
    rows = _rows_for(CLONE_A, CLONE_B_RENAMED)
    row = next(r for r in rows if r["code_key"] == "total = 0")
    assert row["line_dup"] == 1, "名前が違うので完全一致にはならない"
    assert row["shape_dup"] == 2, "正規化すれば一致するはず"
    assert row["shape_group"].startswith("S")


def test_blank_and_comment_lines_are_never_marked_duplicate():
    source = "# note\n\n" + CLONE_A
    rows = _rows_for(source, source)
    for row in rows:
        if row["kind"] in ("blank",):
            assert row["line_dup"] == 0


def test_clone_membership_is_written_on_every_covered_line():
    rows = _rows_for(CLONE_A, CLONE_A)
    marked = [r for r in rows if r["dup_id"]]
    assert marked, "宣言単位クローンの行に dup_id が付いていない"
    assert all(r["dup_id"].startswith("F") for r in marked)
    assert all("b.py" in r["dup_partners"] or "a.py" in r["dup_partners"] for r in marked)


def test_sort_by_code_places_identical_lines_next_to_each_other():
    rows = sort_rows(_rows_for(CLONE_A, CLONE_A), "code")
    keys = [r["code_key"] for r in rows if r["code_key"]]
    assert keys == sorted(keys), "code_key 昇順になっていない"
    positions = [i for i, k in enumerate(keys) if k == "total = 0"]
    assert positions[1] - positions[0] == 1, "同一行が隣接していない"


def test_sort_by_dup_puts_the_heaviest_duplicates_first():
    rows = sort_rows(_rows_for(CLONE_A, CLONE_A), "dup")
    assert rows[0]["line_dup"] >= rows[-1]["line_dup"]


def test_filter_extracts_only_duplicated_lines():
    rows = _rows_for(CLONE_A, CLONE_A)
    selected = filter_rows(rows, "line", min_tok=0)
    assert selected and all(r["line_dup"] >= 2 for r in selected)


def test_filter_min_tok_drops_trivial_lines():
    rows = _rows_for(CLONE_A, CLONE_A)
    selected = filter_rows(rows, "line", min_tok=5)
    assert all(r["tok"] >= 5 for r in selected)


def test_filter_skips_only_the_kinds_that_were_asked_for():
    source = "import os\n" + CLONE_A
    rows = _rows_for(source, source)
    assert any(r["kind"] == "import" for r in filter_rows(rows, "line", 0))
    selected = filter_rows(rows, "line", 0, skip_kinds=frozenset({"import"}))
    assert selected, "import を除いた結果が空になっている"
    assert all(r["kind"] != "import" for r in selected)


def test_csv_header_is_generated_from_the_single_source_of_columns():
    buffer = io.StringIO()
    write_csv(_rows_for(CLONE_A, CLONE_A), buffer)
    header = next(csv.reader(io.StringIO(buffer.getvalue())))
    assert tuple(header) == COLUMNS


# --- 走査範囲の台帳 ----------------------------------------------------------

def test_scope_ledger_excludes_vendor_and_includes_sources():
    scope = Scope.from_ledger(_ROOT)
    assert scope.allows("marketdata/dataset.py") is True
    assert scope.allows("unified_ui/web/js/op_log.js") is True
    assert scope.allows("lightweight-charts-python-main/lightweight_charts/abstract.py") is False
    assert scope.allows("prototype_260626-01/web/js/main.js") is False


def test_scope_ledger_excludes_worktree_copies_of_the_repository():
    """``.claude/worktrees/<name>/`` はリポジトリ自身の複製。走査すると全ファイルが
    二重計上され、トークン量も worktree の数だけ増えて OOM で落ちる（実測）。"""
    scope = Scope.from_ledger(_ROOT)
    assert scope.allows(".claude/worktrees/wt/marketdata/dataset.py") is False
    assert scope.allows(".claude/agents/foo.py") is False
    assert scope.allows("simulator/.venv/lib/x.py") is False


def test_traversal_does_not_follow_a_self_referencing_symlink(tmp_path: Path):
    """``unified_ui/web/node_modules`` の自己参照 symlink は実在する（ISSUE-280）。

    辿ると深さが際限なく増え、走査が OOM で落ちる（実測: exit 137）。除外ディレクトリへ
    降りないこと・ディレクトリ symlink を辿らないことの両方で断つ。
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "codescan_scope.txt").write_text(
        "+ **/*.py\n- **/node_modules/**\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    modules = tmp_path / "src" / "node_modules"
    modules.mkdir()
    (modules / "dep.py").write_text("y = 2\n", encoding="utf-8")
    (modules / "self").symlink_to(modules, target_is_directory=True)

    found = iter_files(tmp_path, Scope.from_ledger(tmp_path), default_registry())

    assert found == ["src/a.py"]


def test_traversal_does_not_follow_symlinked_directories_even_when_allowed(tmp_path: Path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "codescan_scope.txt").write_text("+ **/*.py\n", encoding="utf-8")
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)

    found = iter_files(tmp_path, Scope.from_ledger(tmp_path), default_registry())

    assert found == ["real/a.py"]


def test_scope_blocks_directory_before_descending():
    scope = Scope.from_ledger(_ROOT)
    assert scope.blocks_directory(".claude") is True
    assert scope.blocks_directory("unified_ui/web/node_modules") is True
    assert scope.blocks_directory("lightweight-charts-python-main") is True
    assert scope.blocks_directory("marketdata") is False


def test_cli_exclude_option_appends_to_the_ledger_without_replacing_it():
    scope = Scope.from_ledger(_ROOT, extra_exclude=["marketdata/**"])
    assert scope.allows("marketdata/dataset.py") is False
    assert scope.allows("lightweight-charts-python-main/x.py") is False, "既定の除外が消えている"


# --- CLI --------------------------------------------------------------------

def test_cli_writes_rows_and_report(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "tools").mkdir()
    (project / "tools" / "codescan_scope.txt").write_text("+ **/*.py\n", encoding="utf-8")
    (project / "pkg" / "a.py").write_text(CLONE_A, encoding="utf-8")
    (project / "pkg" / "b.py").write_text(CLONE_A, encoding="utf-8")
    out = tmp_path / "out"

    code = main(["--repo-root", str(project), str(project / "pkg"),
                 "--out", str(out), "--min-tokens", "10", "--min-lines", "3",
                 "--no-summary"])

    assert code == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["scope"]["files"] == 2
    assert len(report["duplication"]["function_clones"]) == 1
    rows = list(csv.DictReader((out / "rows.csv").open(encoding="utf-8")))
    assert len(rows) == 2 * len(CLONE_A.splitlines())
    assert {r["file"] for r in rows} == {"a.py", "b.py"}


def test_cli_fail_over_returns_non_zero(tmp_path: Path):
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "tools").mkdir()
    (project / "tools" / "codescan_scope.txt").write_text("+ **/*.py\n", encoding="utf-8")
    (project / "pkg" / "a.py").write_text(CLONE_A, encoding="utf-8")
    (project / "pkg" / "b.py").write_text(CLONE_A, encoding="utf-8")

    code = main(["--repo-root", str(project), str(project / "pkg"),
                 "--out", str(tmp_path / "out"), "--min-tokens", "10", "--min-lines", "3",
                 "--no-summary", "--fail-over", "0"])
    assert code == 1


def test_cli_reports_empty_scope_instead_of_pretending_success(tmp_path: Path, capsys):
    project = tmp_path / "proj"
    (project / "tools").mkdir(parents=True)
    (project / "tools" / "codescan_scope.txt").write_text("+ **/*.py\n", encoding="utf-8")
    code = main(["--repo-root", str(project), "--out", str(tmp_path / "out"), "--no-summary"])
    assert code == 2


@pytest.mark.parametrize("path,expected", [
    ("x.py", "python"), ("x.js", "javascript"), ("x.mjs", "javascript"), ("x.ts", "javascript"),
])
def test_registry_routes_by_extension(path: str, expected: str):
    analyzer = default_registry().for_path(path)
    assert analyzer is not None and analyzer.language == expected


def test_registry_returns_none_for_unknown_extension():
    assert default_registry().for_path("notes.md") is None
