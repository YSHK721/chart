"""足の時刻を足ごとに 1 本ずつ epoch 秒へ変換する形を機械的に禁じるゲート（ISSUE-553 のテストの穴）。

何が問題か（事後レビュー 2026-09-29）:
    足の時刻の一括変換（`simulator/usecase/bar_times.py`）の計算量検定は、観測口（「`set_observer`」）を
    通った変換しか数えない。観測口を迂回して足ごとに 「`epoch_seconds`」 を呼ぶ形（例:
    「`chart_overlay_writer`」 を ``[epoch_seconds(b.time) for b in bars]`` に戻す）は出力が同じなので
    どの検定も緑のまま通る。ISSUE-553 では 4 箇所のこの形が 1 run で計 1,290 万回・cProfile 65 秒を使っていた。

本モジュールが固定する契約（AST 走査・`test_epoch_conversion_single_source.py` と同じ方式）:
  1. **本番コードに無いこと**: `simulator/`（tests を除く）で、ループ・内包表記の中から
     ループ変数を含む式の ``.time`` を 「`epoch_seconds`」 へ渡す形が、宣言した例外の外に 1 つも無い。
     取引ごとの変換（``t.entry_time`` など ``.time`` 以外の属性）は対象外。
  2. **例外は理由と ISSUE 番号つきの宣言だけ**: 宣言は (ファイル, 関数) 単位。宣言が指す場所に
     違反が実在すること（直した日に宣言が空振りのまま残らない）。
  3. **ゲート自身の検出力**: 違反の形を注入したソースを実際に検出し、対象外の形を検出しない。

構造: Arrange-Act-Assert。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: リポジトリ内の `simulator` パッケージ本体。
_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: 変換関数の名前（domain の単一ソース・`simulator/domain/bar_time.py`）。
_CONVERTER = "epoch_seconds"

#: 足の時刻の属性名（`simulator/domain/bar.py` の Bar）。
_BAR_TIME_ATTR = "time"

#: 足ごとの変換を許す場所 → 理由（ISSUE 番号つき）。キーは (`_SIMULATOR_DIR` からの相対 posix パス, 関数の限定名)。
_DECLARED_EXCEPTIONS: "dict[tuple[str, str], str]" = {
    (
        "adapter/calendar/session_calendar.py",
        "Jp225SessionCalendar.closed_bar_indices",
    ): (
        "ISSUE-553: 足ごとに pd.Timestamp で時分を取るループの中の変換。sim の実行経路は既定の"
        " NullCalendar で通らない（「`session_calendar`」=jp225 の照合・最適化・walk-forward だけ）。"
        " 実測 2026-09-29: 1 run に 1 回・足 28,097 本で 28,097 回・0.165 秒。是正は本件の触れてよい範囲の外"
    ),
}


def _production_files() -> "list[Path]":
    """走査対象＝ `simulator/` 配下の本番 .py 全部（tests / __pycache__ を除く）。"""
    return sorted(
        p
        for p in _SIMULATOR_DIR.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )


def _converter_aliases(tree: ast.AST) -> "set[str]":
    """変換関数を指す名前（``from ... import epoch_seconds as es`` の別名を含む）。"""
    names = {_CONVERTER}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _CONVERTER and alias.asname:
                    names.add(alias.asname)
    return names


def _is_converter_call(node: ast.AST, aliases: "set[str]") -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in aliases
    return isinstance(func, ast.Attribute) and func.attr == _CONVERTER


def _names_in(node: ast.AST) -> "set[str]":
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _bar_time_args(call: ast.Call, loop_names: "set[str]") -> bool:
    """引数に「ループ変数を含む式の ``.time``」があるか。"""
    return any(
        isinstance(arg, ast.Attribute)
        and arg.attr == _BAR_TIME_ATTR
        and _names_in(arg.value) & loop_names
        for arg in call.args
    )


class _Finder(ast.NodeVisitor):
    """ループ・内包表記の中の足ごとの変換を (行番号, 関数の限定名) で集める。"""

    def __init__(self, aliases: "set[str]") -> None:
        self._aliases = aliases
        self._scope: "list[str]" = []
        self.found: "list[tuple[int, str]]" = []

    def _visit_scope(self, node) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit_scope

    def _check(self, bodies: "list[ast.AST]", loop_names: "set[str]") -> None:
        for body in bodies:
            for node in ast.walk(body):
                if _is_converter_call(node, self._aliases) and _bar_time_args(node, loop_names):
                    self.found.append((node.lineno, ".".join(self._scope) or "<module>"))

    def visit_For(self, node) -> None:
        self._check(node.body, _names_in(node.target))
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def _visit_comprehension(self, node) -> None:
        loop_names: "set[str]" = set()
        for gen in node.generators:
            loop_names |= _names_in(gen.target)
        parts = [getattr(node, name) for name in ("elt", "key", "value") if hasattr(node, name)]
        parts += [cond for gen in node.generators for cond in gen.ifs]
        self._check(parts, loop_names)
        self.generic_visit(node)

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _visit_comprehension


def _per_bar_conversions(source: str) -> "list[tuple[int, str]]":
    tree = ast.parse(source)
    finder = _Finder(_converter_aliases(tree))
    finder.visit(tree)
    # 入れ子のループで同じ呼出を 2 度数えない。
    return sorted(set(finder.found))


def _violations_by_place() -> "dict[tuple[str, str], list[int]]":
    places: "dict[tuple[str, str], list[int]]" = {}
    for path in _production_files():
        rel = path.relative_to(_SIMULATOR_DIR).as_posix()
        for line, scope in _per_bar_conversions(path.read_text(encoding="utf-8")):
            places.setdefault((rel, scope), []).append(line)
    return places


class TestTheGateOverProductionCode:
    """契約 1・2: 本番コード全体の走査。"""

    def test_no_per_bar_conversion_exists_outside_the_declared_exceptions(self):
        violations = {
            f"{rel}:{lines} ({scope})"
            for (rel, scope), lines in _violations_by_place().items()
            if (rel, scope) not in _DECLARED_EXCEPTIONS
        }
        assert violations == set(), (
            "足の時刻を足ごとに epoch_seconds へ渡す形が本番コードに現れました。"
            "Bar 列は simulator.usecase.bar_times.bar_epoch_seconds で一括変換してください"
            f"（ISSUE-553）: {sorted(violations)}"
        )

    def test_every_declared_exception_still_points_at_a_real_site(self):
        stale = set(_DECLARED_EXCEPTIONS) - set(_violations_by_place())
        assert stale == set(), f"例外の宣言が空振りしています（直したなら宣言を外す）: {sorted(stale)}"

    @pytest.mark.parametrize("place", sorted(_DECLARED_EXCEPTIONS))
    def test_every_declared_exception_carries_an_issue_number(self, place):
        assert "ISSUE-" in _DECLARED_EXCEPTIONS[place]


class TestTheGateHasDetectionPower:
    """契約 3: 注入した違反をゲートが実際に検出し、対象外を検出しない。"""

    @pytest.mark.parametrize(
        "snippet",
        [
            # ISSUE-553 で直した形そのもの（chart_overlay_writer の差し戻し）
            "def f(bars):\n    return [epoch_seconds(b.time) for b in bars]\n",
            "def f(bars):\n    return {epoch_seconds(b.time): b for b in bars}\n",
            "def f(bars):\n    return list(epoch_seconds(b.time) for b in bars)\n",
            "def f(bars, w):\n    return [b for b in bars if w.contains(epoch_seconds(b.time))]\n",
            "def f(bars):\n    for i, bar in enumerate(bars):\n        x = epoch_seconds(bar.time)\n",
            "def f(bars):\n    for i in range(len(bars)):\n        x = epoch_seconds(bars[i].time)\n",
            "def f(bars):\n    return [bar_time.epoch_seconds(b.time) for b in bars]\n",
            "from simulator.domain.bar_time import epoch_seconds as es\n"
            "def f(bars):\n    return [es(b.time) for b in bars]\n",
        ],
    )
    def test_per_bar_conversions_are_detected(self, snippet):
        assert len(_per_bar_conversions(snippet)) == 1

    def test_the_place_is_named_by_the_enclosing_function(self):
        snippet = "class C:\n    def g(self, bars):\n        return [epoch_seconds(b.time) for b in bars]\n"
        assert _per_bar_conversions(snippet) == [(3, "C.g")]

    @pytest.mark.parametrize(
        "snippet",
        [
            # 一括変換の受け口（1 本ずつの委譲は時刻の列を回す・足の .time ではない）
            "def f(times):\n    return [epoch_seconds(t) for t in times]\n",
            # 取引ごとの変換は対象外
            "def f(trades):\n    return [epoch_seconds(t.entry_time) for t in trades]\n",
            # ループの外の 1 本（窓の端・先頭と末尾の足）
            "def f(bar, bars):\n    return epoch_seconds(bar.time), epoch_seconds(bars[0].time)\n",
            # ループ変数を含まない .time（ループの中でも 1 本ずつではない）
            "def f(bars, first):\n    for b in bars:\n        x = epoch_seconds(first.time)\n",
        ],
    )
    def test_out_of_scope_forms_are_not_flagged(self, snippet):
        assert _per_bar_conversions(snippet) == []
