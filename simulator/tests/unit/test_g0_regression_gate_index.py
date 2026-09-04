"""G0 回帰ゲート索引（ISSUE-479 Wave2 フェーズ 0-1・加法のみ）。

固定する仕様:
    数値不変（byte 等価）を守る回帰ゲートの集合 **G0** を、コードの中に 1 箇所だけ
    明文化し、その構成要素が 1 つでも欠けたら赤になる。

なぜ索引が要るか（構造的理由）:
    G0 は「複数ファイルに散った検定の集合」であり、集合としての実体をどこも持って
    いなかった。集合が暗黙だと、リファクタリングの過程で

      * G0 のファイルが改名・移動されて参照が切れる
      * G0 の中の 1 本（例: fingerprint の MT5 突合再現）だけが消える

    のどちらも「全体は緑」のまま通過する。実際 `pytest simulator/tests -q` は
    「今そこに在る検定」を全部通すだけなので、**消えた検定の不在は原理的に検出できない**。
    索引はその不在を検出する唯一の装置である（同型の考え方: 品質ゲートの baseline が
    「解消済み違反の残留」を stale として赤にするのと対称に、こちらは「在るべき検定の
    消失」を赤にする）。

なぜ構文木で測るか:
    G0 の各ファイルを実行して数えると、索引自身が G0 を回すことになり（入れ子実行）
    測定が重くなるうえ、collection error のある状態では索引すら動かせない。検定の
    所在は構文木にしか現れないので、構文木を読む（`test_layer_dependency_direction.py`
    と同じ方針）。

宣言（`_G0_GATES`）の読み方:
    `required_tests` は「この検定が消えたら G0 が痩せる」と判定した代表点である。
    空タプルは「ファイル全数が G0」（G0-c/d/e）を意味し、その場合は**検定が 1 本でも
    在ること**（空ファイル化・全滅の検出）だけを固定する。件数そのものは焼き込まない
    （件数を期待値にすると、検定を足すたびに索引が赤くなり、索引が「増加の妨害装置」に
    化ける）。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest

#: リポジトリ根（このファイル: <repo>/simulator/tests/unit/ → parents[3]）。
_REPO = Path(__file__).resolve().parents[3]

#: G0 が守る対象スイートの根（G0-f）。
_SIMULATOR_TESTS = "simulator/tests"


@dataclass(frozen=True)
class G0Gate:
    """G0 の構成要素 1 つ。"""

    key: str
    path: str
    why: str
    required_tests: "tuple[str, ...]" = field(default=())


#: G0 の唯一の宣言。ここに無いものは G0 ではなく、ここに在るものは消せない。
_G0_GATES: "tuple[G0Gate, ...]" = (
    G0Gate(
        key="G0-a",
        path="simulator/tests/integration/test_run_backtest_fingerprint.py",
        why="stats/trades の sha256 指紋（既定プロファイル A・trading_start プロファイル B）"
            "＋決定性＋MT5 突合の再現。数値が 1 bit でも動けば赤になる最終防波堤。",
        required_tests=(
            "TestRunBacktestNumericFingerprint::test_the_default_profile_matches_the_known_fingerprint",
            "TestRunBacktestNumericFingerprint::test_the_run_is_deterministic_across_invocations",
            "TestRunBacktestHonoursTradingStart::test_passing_trading_start_changes_the_result",
            "TestRunBacktestHonoursTradingStart::test_the_trading_start_run_matches_the_known_fingerprint",
            "TestRunBacktestHonoursTradingStart::test_the_trading_start_run_reproduces_the_mt5_reconcile_observation",
        ),
    ),
    G0Gate(
        key="G0-b",
        path="simulator/tests/integration/test_ma_slope_reconcile.py",
        why="MA-Slope の MT5 突合（約定・価格・セッション境界）と EquityStats 突合。"
            "指紋が捉えない『MT5 との乖離』の側を守る。",
        required_tests=(
            "TestMaSlopeReconcile::test_mt5_oracle_round_trip_count_is_1163",
            "TestMaSlopeReconcile::test_buy_trades_match_entry_and_exit_price_fully",
            "TestMaSlopeReconcile::test_sell_trades_match_entry_and_exit_price_fully",
            "TestMaSlopeReconcile::test_no_spurious_sell_at_session_boundary_01_00",
            "TestMaSlopeReconcile::test_stop_out_fires_on_sell_at_mt5_bar_13_07",
            "TestMaSlopeEquityStatsReconcile::test_equity_dd_abs_matches_mt5_tightly",
            "TestMaSlopeEquityStatsReconcile::test_recovery_factor_equity_based_matches_mt5_within_residual",
        ),
    ),
    G0Gate(
        key="G0-c",
        path="simulator/tests/unit/test_run_backtest.py",
        why="bar 経路 Interactor の全数。run_backtest の構造変更が最初に触る面。",
    ),
    G0Gate(
        key="G0-d",
        path="simulator/tests/unit/test_run_backtest_every_tick.py",
        why="tick 経路（every_tick）の全数。bar 経路と対称に守る。",
    ),
    G0Gate(
        key="G0-e",
        path="simulator/tests/regression/test_settings_determinism.py",
        why="settings 由来 run の決定性（同一入力→同一出力）。",
    ),
)


def _read_source(path: Path) -> str:
    """索引の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


def _tests_in_source(source: str, filename: str = "<source>") -> "list[str]":
    """ソース 1 本に定義された検定を `Class::name` / `name` で列挙する。

    事前条件: `source` は構文的に妥当な Python。
    事後条件: モジュール直下の `test*` 関数と、クラス直下の `test*` メソッドを返す。
    """
    tree = ast.parse(source, filename=filename)
    out: "list[str]" = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out.append(node.name)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    out.append(f"{node.name}::{sub.name}")
    return out


def _index_scan_over(gates, read=None) -> "tuple[list[Path], dict[str, list[str]]]":
    """宣言された G0 を走査する。**1 ゲートにつき読込 1 回**。

    読込点を差し替えられるのは、計算量検定が「読み捨てが無い」ことを数えるためである。
    事後条件: `(読んだパスの並び, key -> 欠落した検定名の並び)`。
    """
    reader = read or _read_source
    scanned: "list[Path]" = []
    missing: "dict[str, list[str]]" = {}
    for gate in gates:
        path = _REPO / gate.path
        if not path.is_file():
            missing[gate.key] = [f"<ファイルが無い> {gate.path}"]
            continue
        scanned.append(path)
        present = set(_tests_in_source(reader(path), filename=gate.path))
        gaps = [name for name in gate.required_tests if name not in present]
        if not present:
            gaps.append("<検定が 1 本も無い>")
        if gaps:
            missing[gate.key] = gaps
    return scanned, missing


def _index_scan() -> "tuple[list[Path], dict[str, list[str]]]":
    return _index_scan_over(_G0_GATES)


class TestTheG0SetIsComplete:
    """G0 の構成要素が 1 つも欠けていないこと。"""

    def test_every_declared_g0_gate_is_present_with_its_required_tests(self):
        _, missing = _index_scan()
        assert missing == {}, (
            "G0 から欠落している回帰ゲート:\n  "
            + "\n  ".join(f"{key}: {', '.join(gaps)}" for key, gaps in sorted(missing.items()))
            + "\n  G0 は byte 等価を守る集合です。消す場合は設計書の G0 宣言ごと更新してください。"
        )

    @pytest.mark.parametrize("gate", _G0_GATES, ids=lambda g: g.key)
    def test_the_gate_file_lives_under_the_simulator_suite(self, gate):
        # G0-f「simulator 全スイート」で必ず回ること（別の根へ移されたら赤）。
        assert gate.path.startswith(_SIMULATOR_TESTS + "/"), gate.path

    def test_the_whole_simulator_suite_is_itself_part_of_g0(self):
        # G0-f。スイート根が消える／改名されるのも G0 の欠落である。
        assert (_REPO / _SIMULATOR_TESTS).is_dir()


class TestTheIndexHasDetectionPower:
    """索引が空振りしていないこと（恒真式に退化していないこと）。"""

    def test_the_index_declares_every_g0_key_exactly_once(self):
        keys = [gate.key for gate in _G0_GATES]
        assert sorted(keys) == ["G0-a", "G0-b", "G0-c", "G0-d", "G0-e"]
        assert len(set(keys)) - len(keys) == 0

    def test_a_missing_file_is_reported(self):
        gate = G0Gate(key="X", path="simulator/tests/does_not_exist.py", why="")
        _, missing = _index_scan_over((gate,))
        assert missing["X"] == ["<ファイルが無い> simulator/tests/does_not_exist.py"]

    def test_a_removed_required_test_is_reported(self):
        gate = G0Gate(
            key="X",
            path=_G0_GATES[0].path,
            why="",
            required_tests=("TestRunBacktestNumericFingerprint::test_that_never_existed",),
        )
        _, missing = _index_scan_over((gate,))
        assert missing["X"] == [
            "TestRunBacktestNumericFingerprint::test_that_never_existed"
        ]

    def test_an_emptied_gate_file_is_reported(self, tmp_path):
        # 全数ゲート（required_tests 空）でも「検定が 1 本も無い」状態は赤にする。
        path = tmp_path / "empty_gate.py"
        path.write_text("x = 1\n", encoding="utf-8")
        _, missing = _index_scan_over((G0Gate(key="X", path=str(path), why=""),))
        assert missing["X"] == ["<検定が 1 本も無い>"]

    def test_a_populated_whole_file_gate_is_accepted(self, tmp_path):
        path = tmp_path / "full_gate.py"
        path.write_text("def test_x():\n    pass\n", encoding="utf-8")
        _, missing = _index_scan_over((G0Gate(key="X", path=str(path), why=""),))
        assert missing == {}

    def test_class_and_module_level_tests_are_both_discovered(self):
        source = (
            "def test_module_level():\n    pass\n"
            "class TestGroup:\n    def test_in_class(self):\n        pass\n"
            "def helper():\n    pass\n"
        )
        assert _tests_in_source(source) == ["test_module_level", "TestGroup::test_in_class"]

    def test_a_non_test_function_is_not_counted_as_a_gate(self):
        assert _tests_in_source("def helper():\n    pass\n") == []


class TestTheIndexDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_every_scanned_gate_file_is_read_exactly_once(self):
        reads: "list[Path]" = []
        scanned, _ = _index_scan_over(
            _G0_GATES, read=lambda p: (reads.append(p), _read_source(p))[1]
        )
        # 発行（読込）− 使用（判定に用いたファイル）= 0。読み捨てが 1 件も無い。
        assert len(reads) - len(scanned) == 0
        assert len(set(scanned)) - len(scanned) == 0  # 同じファイルを二度読まない

    def test_the_read_count_is_determined_by_the_gate_count_alone(self, tmp_path):
        """宣言 3 件 / 6 件の 2 点で「読込数 == 宣言数」（オーダーの表明）。"""
        measured = {}
        for count in (3, 6):
            gates = []
            for i in range(count):
                path = tmp_path / f"g{count}_{i}.py"
                path.write_text("def test_x():\n    pass\n", encoding="utf-8")
                gates.append(G0Gate(key=f"K{i}", path=str(path), why=""))
            reads: "list[Path]" = []
            scanned, _ = _index_scan_over(
                gates, read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1]
            )
            measured[count] = (len(reads), len(scanned), count)
        for count, (reads_done, scanned_done, declared) in measured.items():
            assert reads_done - scanned_done == 0, (count, measured)
            assert scanned_done - declared == 0, (count, measured)
