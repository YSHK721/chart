"""非対象判定の**評価順**を構文木で固定するゲート（ISSUE-511 段階 8-C / 工程 5 レビュー 🟡-2）。

固定する仕様（境界ごとに 2 点だけ・境界は `_BOUNDARIES` の 2 件）:
    1. 一括適用を呼ぶ**非テストの呼び手が 1 件**であること。
    2. その囲み関数が、規則 S の整合検査を**先に**呼ぶこと。

    境界が 2 件あるのは ISSUE-525 で適用点が分かれたためである（設定の語彙を読む宣言は
    写像層が適用し、run 引数だけで判定できる宣言＝N-17 を含む 4 件は合流点
    `simulator.main.build_interactor` が適用する）。N-17 の根拠は「規則 S が先に効く」
    ことなので、N-17 を実際に適用する側でも順序を機械で守らせる。

なぜ在るか（レビューの実測）:
    N-17 の docstring と基本設計 §4.6 は「規則 S を本判定の**直前**に呼ぶ」と書いていたが、
    その 2 行を入れ替えても検定は 1 件も落ちなかった（工程 5 レビュー 変異 M6）。本作業
    ツリーでの追試（2026-09-18。数え方: effective_to_interactor_kwargs の 2 行を入れ替えた
    状態で `marketdata/tests simulator/tests/unit` を全件走らせ、赤になった検定を数えた
    ——1 failed / 4758 passed / 2 xfailed で是正前 baseline と一致＝**新たに赤になった検定は
    0 件**。唯一の赤は既知の ISSUE-517）。

    N-17 が ``data_path is None`` を非発火にしてよい根拠は「規則 S の双条件が**先に**効く」
    ことだけである。順序が機械で守られていなければ、その根拠は文章の中にしか無い。

変異ゲート（本ゲートの検出力の実測 2026-09-18・本作業ツリー。数え方: 各変異を 1 つだけ
適用して `marketdata/tests simulator/tests/unit` を全件走らせ、既知の失敗 1 件
（ISSUE-517 の test_regression_pins_the_measured_bands_on_real_jp225_tick_data）を除いて
新たに赤になった検定を数えた）:

    (a) 写像入口の 2 行を入れ替える           → 新たに赤 **1 件**
        （`test_that_caller_applies_rule_s_first`）。**是正前は 0 件**だった＝これが
        工程 5 レビューの変異 M6 を殺す。
    (b) 規則 S を呼ばない第 2 の呼び手を足す  → 新たに赤 **4 件**
        （呼び手の件数・評価順・入口の名前・抽出器の自己検査）。

    いずれの変異でも、本ゲート以外の検定は 1 件も赤にならなかった（**誤検出 0 件**）。

**「直前」は固定しない**（実装がその強い命題を満たしていないため）。2 行の間に第 3 の文を
挟んでも本ゲートは緑になる。固定するのは「唯一の入口で規則 S が先に効く」——これは実装が
実際に満たす命題であり、かつ N-17 の根拠として必要十分である。**機械が守っていない主張を
docstring に書かない**のが本段の是正の眼目であり、本ゲート自身もその規律に従う。

走査範囲（列挙ではなく構造で決める・時点 2026-09-18）:
    `simulator` 配下の非テスト `.py`（パス成分に `tests` / `__pycache__` を含むものを除く）。
    ファイルの列挙は `test_package_import_acyclicity._production_files` を**import して使う**
    （同じ走査規則を書き写さない＝「同じコードを手書き複製するな」）。

    **`simulator` の外は走査しない。** 2026-09-18 時点でリポジトリ全体の `.py` を grep して
    apply_unsupported_rules の出現を数えたところ、非テストの出現は定義 1 件・再輸出
    （`simulator/main/tester_settings/__init__.py`）・呼出 1 件のみで `simulator` の外は
    0 件だったが、**それは本ゲートが機械で固定する範囲ではない**（未検証の一般化を宣言に
    書かない）。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from simulator.tests.unit.test_package_import_acyclicity import _production_files


@dataclass(frozen=True)
class _Boundary:
    """保証境界 1 つぶんの評価順の宣言。

    ``rules_call``  非対象判定を一括適用する関数の名前。
    ``rule_s_call`` その**前に**呼ばれねばならない規則 S の整合検査の名前。
    ``entry_point`` 唯一の入口として期待する囲み関数（回帰の錨）。
    """

    rules_call: str
    rule_s_call: str
    entry_point: str


#: 固定する境界。**2 件ある**のは ISSUE-525 で適用点が分かれたためである——設定の語彙を
#: 読む宣言は写像層が適用し、run 引数だけで判定できる宣言（N-17 を含む）は合流点が適用する。
#: N-17 が ``data_path is None`` を非発火にしてよい根拠は「規則 S の双条件が**先に**効く」
#: ことだけなので、N-17 を適用する側（合流点）でも同じ順序を機械で守らせる必要がある。
_BOUNDARIES: "tuple[_Boundary, ...]" = (
    _Boundary(
        rules_call="apply_unsupported_rules",
        rule_s_call="verify_data_consistency",
        entry_point="effective_to_interactor_kwargs",
    ),
    _Boundary(
        rules_call="apply_run_scope_unsupported_rules",
        rule_s_call="verify_engine_data_consistency",
        entry_point="build_interactor",
    ),
)

#: 抽出器そのものを測る合成ソース（下の検出力の検定）が使う境界。
_PROBE_BOUNDARY = _BOUNDARIES[0]


def _called_name(node: ast.Call) -> "str | None":
    """呼出の被呼出名（``f()`` と ``m.f()`` を同じ名前へ畳む）。

    属性形も数えるのは、`import unsupported` して ``unsupported.apply_unsupported_rules()``
    と書く第 2 の呼び手を取り逃さないためである。
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _ordered_call_names(node: ast.AST) -> "list[str]":
    """``node`` の中の呼出名を、書かれている順（行番号順）に並べる。"""
    found: "list[tuple[int, str]]" = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        name = _called_name(call)
        if name is not None:
            found.append((call.lineno, name))
    return [name for _lineno, name in sorted(found)]


class _Caller:
    """一括適用を呼ぶ 1 か所（囲み関数と、その中の呼出の並び）。"""

    def __init__(
        self, where: str, function: str, call_names: "list[str]", boundary: _Boundary
    ) -> None:
        self.where = where
        self.function = function
        self.call_names = call_names
        self.boundary = boundary

    def applies_rule_s_first(self) -> bool:
        """規則 S が一括適用より先に呼ばれているか。"""
        if self.boundary.rule_s_call not in self.call_names:
            return False
        return self.call_names.index(self.boundary.rule_s_call) < self.call_names.index(
            self.boundary.rules_call
        )

    def __repr__(self) -> str:
        return f"{self.where}::{self.function}{self.call_names}"


def _callers_in_source(
    source: str, where: str, boundary: _Boundary = _PROBE_BOUNDARY
) -> "list[_Caller]":
    """``source`` の中で当該境界の一括適用を呼んでいる箇所を列挙する。

    関数の中の呼出と、どの関数にも囲まれていない module 直下の呼出の両方を数える
    （module 直下へ退避されても呼び手は消えない）。
    """
    tree = ast.parse(source, filename=where)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    inside_functions = {
        id(call)
        for function in functions
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
    }

    out = [
        _Caller(where, function.name, _ordered_call_names(function), boundary)
        for function in functions
        if boundary.rules_call in _ordered_call_names(function)
    ]
    module_level = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and id(call) not in inside_functions
        and _called_name(call) == boundary.rules_call
    ]
    if module_level:
        out.append(_Caller(where, "<module>", _ordered_call_names(tree), boundary))
    return out


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


def _scan(
    files, read=None, boundary: _Boundary = _PROBE_BOUNDARY
) -> "tuple[list[Path], list[_Caller]]":
    """``files`` を 1 ファイルにつき**読込 1 回**で走査する。

    読込点を引数で差し替えられるようにしてあるのは、計算量検定が「読み捨てが無い」ことを
    数えるためである（免除リストを持たない＝走査ファイル数 == 判定に使ったファイル数）。
    """
    reader = read or _read_source
    scanned: "list[Path]" = []
    callers: "list[_Caller]" = []
    for path in files:
        scanned.append(path)
        callers.extend(_callers_in_source(reader(path), str(path), boundary))
    return scanned, callers


def _production_scan(
    boundary: _Boundary = _PROBE_BOUNDARY,
) -> "tuple[list[Path], list[_Caller]]":
    return _scan(_production_files(), boundary=boundary)


# --- 固定する仕様 -----------------------------------------------------------------


@pytest.mark.parametrize("boundary", _BOUNDARIES, ids=lambda b: b.rules_call)
class TestTheOnlyEntryPointAppliesRuleSFirst:
    """各境界について、非テストの呼び手は 1 件で、そこで規則 S が先に効く。"""

    def test_there_is_exactly_one_non_test_caller(self, boundary):
        """呼び手が 2 件以上に増えたら落ちる（第 2 の入口は評価順の保証を持たない）。"""
        # Arrange / Act
        _scanned, callers = _production_scan(boundary)
        # Assert
        assert len(callers) == 1, (
            f"{boundary.rules_call} の非テスト呼び手が 1 件ではない: "
            + ", ".join(sorted(repr(caller) for caller in callers))
            + "。呼び手を増やす場合、その囲み関数でも規則 S を先に呼ぶ必要がある"
            "（N-17 が data_path is None を非発火にする根拠が規則 S の双条件だから）。"
        )

    def test_that_caller_applies_rule_s_first(self, boundary):
        """その 1 件が規則 S を先に呼ぶ（2 行を入れ替えたら落ちる）。"""
        # Arrange / Act
        _scanned, callers = _production_scan(boundary)
        caller = callers[0]
        # Assert
        assert caller.applies_rule_s_first(), (
            f"規則 S（{boundary.rule_s_call}）が {boundary.rules_call} より先に"
            f"呼ばれていない: {caller!r}"
        )

    def test_the_caller_is_the_declared_entry_point(self, boundary):
        """呼び手が宣言した入口であること（回帰の錨。名前が変わったら宣言側も直す）。"""
        # Arrange / Act
        _scanned, callers = _production_scan(boundary)
        # Assert
        assert callers[0].function == boundary.entry_point


# --- ゲートが空振りしていないこと ---------------------------------------------------


class TestTheGateHasDetectionPower:
    """測り方が恒真式へ退化していないこと（probe は合成ソース）。"""

    def test_the_scan_covers_production_files(self):
        """走査が 0 件でないこと（0 件なら上の一致は空集合どうしで緑になる）。"""
        # Arrange / Act
        scanned, _callers = _production_scan()
        # Assert
        assert len(scanned) > 0

    def test_the_real_caller_is_found_by_the_extractor(self):
        """実コードから呼び手を 1 件見つけている（抽出器が常に空を返していない）。"""
        # Arrange / Act
        _scanned, callers = _production_scan()
        # Assert
        assert [caller.function for caller in callers] == [_PROBE_BOUNDARY.entry_point]

    def test_a_swapped_order_is_detected(self):
        """変異 (a): 2 行を入れ替えると「先に効く」が偽になる。"""
        # Arrange
        source = (
            "def entry(effective, binding):\n"
            "    apply_unsupported_rules(effective, binding)\n"
            "    verify_data_consistency(effective, has_data=True)\n"
        )
        # Act
        callers = _callers_in_source(source, "probe_swapped.py")
        # Assert
        assert [caller.applies_rule_s_first() for caller in callers] == [False]

    def test_the_correct_order_is_accepted(self):
        """正しい順は真になる（誤検出の対照）。"""
        # Arrange
        source = (
            "def entry(effective, binding):\n"
            "    verify_data_consistency(effective, has_data=True)\n"
            "    apply_unsupported_rules(effective, binding)\n"
        )
        # Act
        callers = _callers_in_source(source, "probe_ordered.py")
        # Assert
        assert [caller.applies_rule_s_first() for caller in callers] == [True]

    def test_a_second_caller_that_skips_rule_s_is_detected(self):
        """変異 (b): 規則 S を呼ばない第 2 の呼び手を足すと、件数と順序の両方で落ちる。"""
        # Arrange
        ordered = (
            "def entry(effective, binding):\n"
            "    verify_data_consistency(effective, has_data=True)\n"
            "    apply_unsupported_rules(effective, binding)\n"
        )
        second = (
            "def another_entry(effective, binding):\n"
            "    apply_unsupported_rules(effective, binding)\n"
        )
        # Act
        callers = _callers_in_source(ordered, "a.py") + _callers_in_source(second, "b.py")
        # Assert
        assert len(callers) == 2
        assert [caller.applies_rule_s_first() for caller in callers] == [True, False]

    def test_a_module_level_caller_is_detected(self):
        """module 直下へ退避した呼び手も数える（関数の外へ出しても辺は消えない）。"""
        # Arrange
        source = "apply_unsupported_rules(EFFECTIVE, BINDING)\n"
        # Act
        callers = _callers_in_source(source, "probe_module_level.py")
        # Assert
        assert [caller.function for caller in callers] == ["<module>"]

    def test_an_attribute_call_is_detected(self):
        """``module.apply_unsupported_rules()`` 形の呼出も数える。"""
        # Arrange
        source = (
            "def entry(effective, binding):\n"
            "    unsupported.apply_unsupported_rules(effective, binding)\n"
        )
        # Act
        callers = _callers_in_source(source, "probe_attribute.py")
        # Assert
        assert [caller.function for caller in callers] == ["entry"]

    def test_a_file_without_the_call_yields_no_caller(self):
        """関係の無いファイルを呼び手と数えない（誤検出の対照）。"""
        # Arrange
        source = "def entry(effective, binding):\n    return verify_data_consistency(effective)\n"
        # Act
        callers = _callers_in_source(source, "probe_unrelated.py")
        # Assert
        assert callers == []


# --- 計算量（発行 − 使用 = 0・規模 2 点） -------------------------------------------


class TestTheGateDoesNotWasteWork:
    """計算量テスト（Test Spy・測るのは時間ではなく回数）。"""

    def test_every_scanned_file_is_read_exactly_once(self):
        """発行（読込）− 使用（走査したファイル）= 0。読み捨てが 1 件も無い。"""
        # Arrange
        reads: "list[Path]" = []
        # Act
        scanned, _callers = _scan(
            _production_files(),
            read=lambda p: (reads.append(p), _read_source(p))[1],
        )
        # Assert
        assert len(scanned) > 0                       # 空振り防止
        assert len(reads) - len(scanned) == 0
        assert len(set(scanned)) - len(scanned) == 0  # 同じファイルを二度走査しない

    @pytest.mark.parametrize("count", [3, 6])
    def test_the_read_count_is_determined_by_the_file_count_alone(self, count, tmp_path):
        """走査対象 3 件 / 6 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。

        **回数そのものは焼き込まない**——焼き込むと、いま何回読んでいるかが仕様へ昇格し、
        無駄を減らす改善まで赤にする。
        """
        # Arrange
        files = []
        for index in range(count):
            path = tmp_path / f"m{count}_{index}.py"
            path.write_text(
                "def entry(effective, binding):\n"
                "    verify_data_consistency(effective, has_data=True)\n"
                "    apply_unsupported_rules(effective, binding)\n",
                encoding="utf-8",
            )
            files.append(path)
        reads: "list[Path]" = []
        # Act
        scanned, callers = _scan(
            files, read=lambda p: (reads.append(p), _read_source(p))[1]
        )
        # Assert
        assert len(callers) == count          # 空振り防止（各ファイルが呼び手を持つ）
        assert len(reads) - len(scanned) == 0
        assert len(scanned) - count == 0
