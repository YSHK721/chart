"""回帰ゲート fixture の生成器が本体に 1 つだけ在ること（ISSUE-479 Wave2 フェーズ 1-D）。

固定する仕様:
    1. ゲートシナリオの定義（gate_scenarios）はリポジトリ内で **1 箇所** にしか無い。
    2. その生成器が作る expected_gate.json は、コミット済みの fixture と **byte 一致**
       する（移設が内容を 1 バイトも変えていない）。
    3. 試作側は本体を指すだけで、生成器の実体を持たない。

なぜ 1 箇所か:
    移設前は、生成器（試作）と回帰ゲート（検定）が同じ 3 シナリオを別々に書いていた。
    片方だけを直せば「fixture は古い条件で作られ、検定は新しい条件で読む」という
    食い違いが起き、しかも検定は緑のまま通る（自分の定義で自分の期待値を読むため）。
    定義を 1 つにすれば、この食い違いは構造的に作れない。

なぜ byte 一致を測るか:
    移設は「所在の変更」であって「内容の変更」ではない。それを主張するには、移設後の
    生成器がコミット済みの生成物を再現することを実測する以外に方法がない。実 tick を
    marketdata から取り直す必要は無い——コミット済みの tick 断片が同じ入力だからである。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.tools.regenerate_account_engine_fixtures import (
    FIXTURE_DIR,
    build_expected,
    expected_gate_json,
    gate_scenarios,
    load_tick_fragment,
)

_REPO = Path(__file__).resolve().parents[3]

#: 生成器の所在（移設先）。
_GENERATOR = "simulator/tools/regenerate_account_engine_fixtures.py"

#: 移設元（スタブとして残す）。
_PROTOTYPE_STUB = "prototype_260811-01/make_regression_fixture.py"


def _parse(path: Path) -> ast.Module:
    """構文木を読む（本検定は文面ではなく構造だけを見る）。"""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_constant(path: Path, name: str):
    """モジュール直下の定数代入の値を構文木から取り出す。"""
    for node in _parse(path).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path} に定数 {name} が無い")


def _files_defining(symbol: str) -> "list[str]":
    """`symbol` を関数として定義しているファイルをリポジトリ全体から列挙する。"""
    found: "list[str]" = []
    for path in sorted(_REPO.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts or "venv" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                found.append(str(path.relative_to(_REPO)))
                break
    return found


class TestTheGateScenariosAreDefinedOnce:
    """シナリオ定義がリポジトリ内で 1 箇所であること。"""

    def test_only_the_generator_defines_the_gate_scenarios(self):
        assert _files_defining("gate_scenarios") == [_GENERATOR]

    def test_the_prototype_points_at_the_relocated_generator(self):
        # 文面ではなく宣言（MOVED_TO 定数）で指す。所在は構文木から読む。
        assert _module_constant(_REPO / _PROTOTYPE_STUB, "MOVED_TO") == _GENERATOR

    def test_the_regression_gate_reads_the_same_definition(self):
        from simulator.tests.unit import test_account_engine_regression as gate

        assert gate._scenarios is gate_scenarios


class TestTheRelocatedGeneratorReproducesTheCommittedFixture:
    """移設が内容を変えていないこと（byte 一致）。"""

    def test_the_regenerated_expected_gate_json_is_byte_identical(self):
        regenerated = expected_gate_json(build_expected(load_tick_fragment()))
        committed = (FIXTURE_DIR / "expected_gate.json").read_text(encoding="utf-8")
        assert regenerated == committed

    def test_the_fixture_directory_is_derived_from_the_repository_root(self):
        """絶対パスを書かない（worktree から本チェックアウトを書き換えないため）。"""
        assert FIXTURE_DIR == _REPO / "simulator" / "tests" / "fixtures" / "account_engine"

    def test_the_generator_contains_no_absolute_path_literal(self):
        absolute = [
            (node.lineno, node.value)
            for node in ast.walk(_parse(_REPO / _GENERATOR))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("/")
        ]
        assert absolute == [], f"絶対パスのリテラルが残っています: {absolute}"

    def test_the_three_scenarios_are_present(self):
        assert sorted(gate_scenarios()) == [
            "G1_long_losscut", "G2_long_stop", "G3_split_partial"
        ]


class TestTheGeneratorDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_each_scenario_runs_the_engine_exactly_once(self):
        seen: "list[str]" = []
        ticks = load_tick_fragment()
        expected = build_expected(ticks, on_scenario=lambda n, r, e: seen.append(n))
        # 発行（エンジン実行）− 使用（出力に入ったシナリオ）= 0。作って捨てる実行が無い。
        assert len(seen) - len(expected) == 0
        assert sorted(seen) == sorted(expected)

    @pytest.mark.parametrize("repeat", [1, 2], ids=["once", "twice"])
    def test_the_tick_fragment_is_read_once_per_request(self, repeat, monkeypatch):
        """読込は要求ごとに 1 回（生成器が内部で二度読みしない）。"""
        opened: "list[Path]" = []
        original = Path.open

        def counting_open(self, *args, **kwargs):
            opened.append(self)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", counting_open)
        for _ in range(repeat):
            load_tick_fragment()
        assert len(opened) - repeat == 0

    def test_the_series_hash_is_computed_once_per_scenario(self):
        """報告のために sha を二度計算していないこと（組み立て済みの値を渡す）。"""
        calls = [
            node.lineno
            for node in ast.walk(_parse(_REPO / _GENERATOR))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "series_sha256"
        ]
        assert len(calls) == 1, f"series_sha256 の呼出点が {len(calls)} 箇所: {calls}"
