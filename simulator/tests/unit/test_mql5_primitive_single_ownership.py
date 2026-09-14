"""MQL5 プリミティブの所有者は 1 つだけ — 再複製を AST 走査で赤にするゲート（ISSUE-445）。

**なぜ規約ではなく検査なのか**: 段階 1・3-B の移植で ``_math_round`` / ``_normalize_double`` /
``_spec_value`` が 3 戦略へ AST 完全一致で手書き複製された（実測）。コメント・規約では
守られなかったため、機械的に検出する。

**検出の仕組み（2 規則・どちらか 1 つでも当たれば違反）**

1. **構造一致**: 戦略ファイル内の全関数（メソッド・ネスト関数を含む）を正規化 AST で
   指紋化し、``mql5_runtime`` が所有する関数の指紋と一致したら違反とする。正規化は
   (a) デコレータ・型注釈・docstring を落とす (b) ``self`` / ``cls`` レシーバ経由の属性
   アクセスを裸の名前に畳む (c) 束縛名（引数・代入先）を出現順の記号へ置換する
   (d) 識別子の先頭アンダースコアを外す — により、``@staticmethod`` 化・``cls._x`` 呼び出し・
   ローカル変数のリネームを跨いで同一と判定する。
2. **同名**: 関数名（先頭アンダースコアを除去）が所有関数名と一致したら違反とする。
   本体を書き換えた「似て非なるコピー」を捕える。

**走査対象をハードコードしない**: 戦略ファイルは ``simulator/adapter/strategy/*.py`` の
glob で拾う（新戦略は自動的に対象に入る）。所有一覧は ``mql5_runtime.__all__`` から導出する
（新プリミティブを所有させれば自動的に検査対象に入る）。どちらの列挙も本ファイルに書かない。

**非空虚性**: ``test_gate_detects_an_injected_private_copy`` が、リネーム済み・
``@classmethod`` 化した private コピーを合成戦略ファイルへ注入して赤になることを実証する。

**射程の限定子（この検査で捕えられないもの）**: 名前も構造も変えた「書き直し」は検出
できない。捕えるのは実際に起きた失敗モード（移植時のコピー & ペースト）である。
また ``_normalize_lot`` のように**戦略ごとに原典が異なるため意図的に重複させる**関数が
存在するため、「戦略ファイル間で重複する関数はすべて違反」という一般規則は採用していない
（採用すると ``_normalize_lot`` / ``_calc_sltp`` / ``_held_sides`` が偽陽性になる）。
"""
from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

import pytest

from simulator.adapter.strategy import mql5_runtime

OWNER_PATH = Path(mql5_runtime.__file__).resolve()
STRATEGY_DIR = OWNER_PATH.parent


# --- AST 正規化 -----------------------------------------------------------------

class _ReceiverStripper(ast.NodeTransformer):
    """``self.foo`` / ``cls.foo`` を裸の ``foo`` に畳む（メソッド形と関数形を同一視する）。"""

    def __init__(self, receiver: "str | None") -> None:
        self._receiver = receiver

    def visit_Attribute(self, node: ast.Attribute):  # noqa: N802 (ast API)
        self.generic_visit(node)
        if (
            self._receiver is not None
            and isinstance(node.value, ast.Name)
            and node.value.id == self._receiver
        ):
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return node


class _Renamer(ast.NodeTransformer):
    """束縛名を記号へ置換し、残りの識別子の先頭アンダースコアを外し、別名を解決する。

    ``aliases`` は「複製と既に判明した関数名 → 所有関数名」の対応。コピー時に呼び出し先まで
    改名された多段リネーム（``cls._math_round`` を ``cls._round_half_up`` に改名したうえで
    それを呼ぶ関数）を、不動点反復で辿るために使う。
    """

    def __init__(self, mapping: "dict[str, str]", aliases: "dict[str, str]") -> None:
        self._mapping = mapping
        self._aliases = aliases

    def visit_Name(self, node: ast.Name):  # noqa: N802 (ast API)
        if node.id in self._mapping:
            node.id = self._mapping[node.id]
        else:
            bare = node.id.lstrip("_")
            node.id = self._aliases.get(bare, bare)
        return node

    def visit_arg(self, node: ast.arg):  # noqa: N802 (ast API)
        node.arg = self._mapping.get(node.arg, node.arg.lstrip("_"))
        node.annotation = None
        node.type_comment = None
        return node

    def visit_Attribute(self, node: ast.Attribute):  # noqa: N802 (ast API)
        self.generic_visit(node)
        node.attr = node.attr.lstrip("_")
        return node

    def visit_keyword(self, node: ast.keyword):  # noqa: N802 (ast API)
        self.generic_visit(node)
        if node.arg is not None:
            node.arg = node.arg.lstrip("_")
        return node


def _bound_names(fn: ast.AST) -> "list[str]":
    """束縛名（引数・代入先）を DFS の出現順で返す。"""
    order: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            order.append(name)

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.arg):
            add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            add(node.id)
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(fn)
    return order


def fingerprint(
    fn: "ast.FunctionDef | ast.AsyncFunctionDef", aliases: "dict[str, str] | None" = None
) -> str:
    """関数の正規化 AST 指紋。名前・デコレータ・注釈・docstring・束縛名に不変。"""
    aliases = aliases or {}
    fn = copy.deepcopy(fn)
    fn.decorator_list = []
    fn.returns = None
    fn.type_comment = None
    if (
        fn.body
        and isinstance(fn.body[0], ast.Expr)
        and isinstance(fn.body[0].value, ast.Constant)
        and isinstance(fn.body[0].value.value, str)
    ):
        fn.body = fn.body[1:] or [ast.Pass()]

    receiver = None
    if fn.args.posonlyargs and fn.args.posonlyargs[0].arg in ("self", "cls"):
        receiver = fn.args.posonlyargs[0].arg
        fn.args.posonlyargs = fn.args.posonlyargs[1:]
    elif fn.args.args and fn.args.args[0].arg in ("self", "cls"):
        receiver = fn.args.args[0].arg
        fn.args.args = fn.args.args[1:]

    fn = _ReceiverStripper(receiver).visit(fn)
    mapping = {name: f"n{i}" for i, name in enumerate(_bound_names(fn))}
    fn = _Renamer(mapping, aliases).visit(fn)
    fn.name = ""
    ast.fix_missing_locations(fn)
    return hashlib.sha256(ast.dump(fn, include_attributes=False).encode()).hexdigest()


# --- 所有一覧（mql5_runtime から導出・本ファイルに列挙しない）--------------------

def owned_primitives() -> "dict[str, str]":
    """``mql5_runtime.__all__`` が公開する関数の {名前: 指紋}。"""
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"))
    exported = set(mql5_runtime.__all__)
    return {
        node.name: fingerprint(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in exported
    }


# --- 走査（対象はハードコードせず glob で拾う）----------------------------------

def strategy_sources() -> "list[Path]":
    """``simulator/adapter/strategy/*.py`` から所有モジュール自身を除いた全ファイル。"""
    return [p for p in sorted(STRATEGY_DIR.glob("*.py")) if p.resolve() != OWNER_PATH]


def find_primitive_reimplementations(
    paths: "list[Path]", owned: "dict[str, str]"
) -> "list[tuple[str, str, str]]":
    """(ファイル名, 関数名, 違反理由) の一覧を返す。空 list なら違反なし。

    ファイル単位で不動点反復する。1 周目で判明した複製（例: ``math_round`` のコピー）を
    別名として登録し、それを呼ぶ関数（例: ``normalize_double`` のコピー）を次の周で拾う。
    別名を張るのは「既に複製と判明した関数」に限るため、無関係な関数を巻き込まない。
    """
    by_fingerprint = {fp: name for name, fp in owned.items()}
    violations: list[tuple[str, str, str]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        aliases: dict[str, str] = {}
        reasons: dict[int, str] = {}
        while True:
            changed = False
            for node in funcs:
                if id(node) in reasons:
                    continue
                bare = node.name.lstrip("_")
                if bare in owned:
                    reasons[id(node)] = f"同名: mql5_runtime.{bare} と衝突する"
                    aliases[bare] = bare
                    changed = True
                    continue
                hit = by_fingerprint.get(fingerprint(node, aliases))
                if hit is not None:
                    reasons[id(node)] = f"構造一致: mql5_runtime.{hit} の再実装"
                    aliases[bare] = hit
                    changed = True
            if not changed:
                break
        violations.extend(
            (path.name, node.name, reasons[id(node)]) for node in funcs if id(node) in reasons
        )
    return violations


# --- 検定 ----------------------------------------------------------------------

def test_all_public_functions_are_declared_in_dunder_all():
    # __all__ が所有一覧の単一ソースであるため、公開関数の書き漏れは検査漏れになる。
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"))
    public = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }

    assert public == set(mql5_runtime.__all__)


def test_owned_primitives_are_discovered_from_the_owner_module():
    # 走査の前提（所有一覧が空でない）を明示する。空なら以下の検査は空虚になる。
    owned = owned_primitives()

    assert set(owned) == set(mql5_runtime.__all__)
    assert len(set(owned.values())) == len(owned)  # 指紋が衝突していない


def test_strategy_files_are_discovered_by_glob_not_by_a_hardcoded_list():
    # 新戦略が黙って対象外にならないことの確認（列挙をハードコードしていない）。
    names = {p.name for p in strategy_sources()}

    assert "mql5_runtime.py" not in names
    assert {"ma_slope.py", "ma_slope_pending.py", "stop_entry_probe.py"} <= names
    assert len(names) >= 8


def test_no_strategy_file_reimplements_an_mql5_primitive():
    # 本ゲートの本体。違反 0 件であること。
    violations = find_primitive_reimplementations(strategy_sources(), owned_primitives())

    assert violations == [], "MQL5 プリミティブの再実装を検出しました: " + "; ".join(
        f"{f}::{fn}（{why}）" for f, fn, why in violations
    )


_INJECTED_COPY = '''
"""負の対照用の合成戦略ファイル。"""
import math


class _FakeStrategy:
    @classmethod
    def _round_half_up(cls, magnitude):
        floor_part = math.floor(abs(magnitude))
        if abs(magnitude) - floor_part >= 0.5:
            floor_part += 1.0
        return math.copysign(floor_part, magnitude)

    @classmethod
    def _to_digits(cls, amount, places):
        factor = 10.0 ** places
        return cls._round_half_up(amount * factor) / factor
'''


def test_gate_detects_an_injected_private_copy(tmp_path):
    # 非空虚性の実証: 関数名も変数名も変え、@classmethod 化した private コピーを注入する。
    # 名前規則では捕まらない（_round_half_up / _to_digits は所有名と一致しない）ため、
    # ここで赤になるのは構造一致規則が効いている証拠である。
    injected = tmp_path / "injected_strategy.py"
    injected.write_text(_INJECTED_COPY, encoding="utf-8")

    violations = find_primitive_reimplementations([injected], owned_primitives())

    reasons = {(fn, why) for _, fn, why in violations}
    assert ("_round_half_up", "構造一致: mql5_runtime.math_round の再実装") in reasons
    assert ("_to_digits", "構造一致: mql5_runtime.normalize_double の再実装") in reasons


def test_gate_detects_a_same_named_copy_whose_body_was_edited(tmp_path):
    # 名前規則の非空虚性: 本体を書き換えても所有名を名乗れば赤になる。
    injected = tmp_path / "renamed_body_strategy.py"
    injected.write_text(
        "class _S:\n"
        "    @staticmethod\n"
        "    def _spec_value(cfg, key):\n"
        "        return float(cfg.get(key, 1.0))\n",
        encoding="utf-8",
    )

    violations = find_primitive_reimplementations([injected], owned_primitives())

    assert [(fn, why) for _, fn, why in violations] == [
        ("_spec_value", "同名: mql5_runtime.spec_value と衝突する")
    ]


def test_gate_does_not_flag_unrelated_strategy_helpers(tmp_path):
    # 偽陽性の限定子: MQL5 プリミティブでない関数は検出しない。
    injected = tmp_path / "plain_strategy.py"
    injected.write_text(
        "class _S:\n"
        "    @staticmethod\n"
        "    def _held_sides(account):\n"
        "        return {p.side for p in getattr(account, 'open_positions', [])}\n",
        encoding="utf-8",
    )

    assert find_primitive_reimplementations([injected], owned_primitives()) == []


@pytest.mark.parametrize("name", ["ma_slope.py", "ma_slope_pending.py", "stop_entry_probe.py"])
def test_normalize_lot_stays_private_to_each_strategy(name):
    # 反対側の固定: 3 原典で挙動が異なる _normalize_lot は共通化してはならない。
    # ゲートがこれを「複製だから消せ」と誘導しないことを明示する。
    tree = ast.parse((STRATEGY_DIR / name).read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "_normalize_lot" in defined
    assert "normalize_lot" not in mql5_runtime.__all__
