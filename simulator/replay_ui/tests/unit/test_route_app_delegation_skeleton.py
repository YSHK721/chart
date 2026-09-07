"""ルート App が**透過委譲を持たない**ことを固定する（ISSUE-502 段階 5B）。

## 本ファイルが固定していた旧規則と、それを差し替えた理由

旧版（ISSUE-479 Wave2 再レビュー 🟡-2）は、5 本のルート App が内側 App を包み
「自分が持たない属性は内側へ委譲する」という同一の骨格（inner / __getattr__）を持つ、
という前提に立ち、**その骨格が 5 本で 1 文字も食い違わないこと**を AST で突き合わせていた。
「重複そのものが害なのではなく、片方だけが書き換わって食い違うことが害である」という判断で、
重複を消さずに食い違いを検出する形を採っていた。

その判断は「透過委譲を持つ」ことを所与としていた。段階 5B はその所与のほうを外した。

透過委譲（__getattr__ で内側へ全属性を流す）の害は、5 本の食い違いではなく
**委譲の欠落がリクエスト時まで露見しないこと**である。同型の壊れ方は
``sim_ui/adapter/causal_compute_ports.py`` が対照実験で実測済みで、明示委譲を 1 面
（period_start）落としても動的フォールバックが拾い、既存検定は緑のまま通った。

現在の形: ルート App は「業務の入口（core）」と「外れた path を渡す先（fallback）」を
**明示で受け取る**。包む関係が無くなったので透過委譲は 1 つも要らない。必要な面は
REQUIRED_CORE_MEMBERS で宣言し、生成時に照合する（欠落は起動時 ``TypeError``）。

## 本ファイルが固定する規則

1. framework 層のどのモジュールにも __getattr__ が定義されていない（再出現の Red）。
2. 全ルート App が REQUIRED_CORE_MEMBERS を宣言し、それが空でない。
3. ルート App が core から引く名は、宣言した面の中にある（宣言外の名を掘らない）。

計算量検定（絶対命令 2026-08-28）: 走査はモジュール 1 本につき parse 1 回
    （発行 − モジュール数 = 0）。モジュール数を変えた 2 点で固定する。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from simulator.replay_ui.framework import (
    serve_replay,
    serve_replay_candles,
    serve_replay_catalog,
    serve_replay_compute,
    serve_replay_intraday,
    serve_replay_profiles,
)

#: 透過委譲の不在を要求する framework モジュール（App を増やしたらここへ 1 行足す）。
_FRAMEWORK_MODULES = (
    serve_replay,
    serve_replay_candles,
    serve_replay_compute,
    serve_replay_intraday,
    serve_replay_profiles,
    serve_replay_catalog,
)

#: ルート App（モジュール, クラス名）。宣言面と core 参照を突き合わせる対象。
_ROUTE_APP_MODULES = (
    (serve_replay_candles, "ReplayCandlesApp"),
    (serve_replay_compute, "ReplayComputeApp"),
    (serve_replay_intraday, "ReplayIntradayApp"),
    (serve_replay_profiles, "ReplayProfilesApp"),
    (serve_replay_catalog, "ReplayCatalogApp"),
)


def _module_path(module) -> Path:
    src = inspect.getsourcefile(module)
    assert src is not None, f"ソースファイルが取れない: {module!r}"
    return Path(src)


def _tree_of(module, *, parse=ast.parse) -> ast.Module:
    """1 モジュールを **1 回だけ** parse して木を返す。"""
    return parse(_module_path(module).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def trees() -> "dict[str, ast.Module]":
    return {m.__name__: _tree_of(m) for m in _FRAMEWORK_MODULES}


# --------------------------------------------------------------------------------------
# 1. 透過委譲の再出現 Red
# --------------------------------------------------------------------------------------
def _getattr_defs(tree: ast.Module) -> "list[str]":
    """__getattr__ を定義しているクラス名を返す（モジュール直下の関数も拾う）。"""
    found: "list[str]" = []
    # モジュール直下の __getattr__（PEP 562・モジュール属性の透過）。
    #   ``ast.walk`` ではなく直下の body を見る——walk はクラス内の定義も同じ節点として
    #   拾うため、同じ 1 件を 2 度数えてしまう（初版が実際にそうなった）。
    found += [
        "<module>"
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
    ]
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == "__getattr__":
                found.append(node.name)
    return found


@pytest.mark.parametrize("module", _FRAMEWORK_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_the_framework_module_defines_no_transparent_delegation(trees, module) -> None:
    """__getattr__ は 1 つも無い。

    透過委譲は「この殻が何を提供するか」を型にも読みにも出さないまま、委譲の欠落を
    リクエスト時まで隠す。必要なものは REQUIRED_CORE_MEMBERS で宣言する。
    """
    defined = _getattr_defs(trees[module.__name__])
    assert defined == [], (
        f"{module.__name__} に __getattr__ が再出現している: {defined}。"
        " 透過委譲は置かない（要求面は REQUIRED_CORE_MEMBERS で宣言する）。"
    )


def test_the_detector_can_see_a_transparent_delegation() -> None:
    """検出器の空振り検定: 変異体（__getattr__ を持つクラス）を与えれば検出する。"""
    mutated = ast.parse(
        "class Wrapper:\n"
        "    def __getattr__(self, name):\n"
        '        return getattr(self._inner, name)\n'
    )
    assert _getattr_defs(mutated) == ["Wrapper"], "検出器が透過委譲を見落としている（ガードが空虚）"


# --------------------------------------------------------------------------------------
# 2. 要求面の宣言（存在と非空）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("module,class_name", _ROUTE_APP_MODULES, ids=lambda v: getattr(v, "__name__", v))
def test_every_route_app_declares_the_core_members_it_requires(module, class_name) -> None:
    """要求面が宣言されており、空でない（宣言が空だと検査が素通りする）。"""
    app_class = getattr(module, class_name)
    declared = getattr(app_class, "REQUIRED_CORE_MEMBERS", None)
    assert isinstance(declared, tuple) and declared, (
        f"{class_name}.REQUIRED_CORE_MEMBERS が宣言されていない（または空）: {declared!r}"
    )
    assert all(isinstance(name, str) and name for name in declared), declared


# --------------------------------------------------------------------------------------
# 3. 宣言外の名を core から掘らない
# --------------------------------------------------------------------------------------
def _core_attribute_names(tree: ast.Module, class_name: str) -> "set[str]":
    """クラス本体で ``self._core.<name>`` として読まれている名を集める。"""
    klass = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name), None
    )
    assert klass is not None, f"クラスが見つからない: {class_name}"
    names: "set[str]" = set()
    for node in ast.walk(klass):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_core"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        ):
            names.add(node.attr)
    return names


@pytest.mark.parametrize("module,class_name", _ROUTE_APP_MODULES, ids=lambda v: getattr(v, "__name__", v))
def test_the_app_reads_only_the_core_members_it_declared(trees, module, class_name) -> None:
    """``self._core.<name>`` で読む名は、すべて宣言済みの要求面に含まれる。

    宣言していない名を掘ると、生成時の照合を素通りして実行時に落ちる（透過委譲と同じ壊れ方）。
    """
    app_class = getattr(module, class_name)
    declared = set(app_class.REQUIRED_CORE_MEMBERS)
    used = _core_attribute_names(trees[module.__name__], class_name)
    undeclared = sorted(used - declared)
    assert undeclared == [], (
        f"{class_name} が宣言外の core メンバーを読んでいる: {undeclared}。"
        f" REQUIRED_CORE_MEMBERS へ足すこと（現在の宣言: {sorted(declared)}）。"
    )


def test_the_core_attribute_scanner_is_not_vacuous(trees) -> None:
    """走査器の空振り検定: 実際に名を採れている（空集合どうしの比較で緑にならない）。"""
    collected = {
        cls: _core_attribute_names(trees[mod.__name__], cls) for mod, cls in _ROUTE_APP_MODULES
    }
    # ReplayComputeApp だけは表引き（getattr(self._core, method)）なので静的には採れない。
    assert collected["ReplayCandlesApp"] >= {"candles", "available_days"}
    assert collected["ReplayProfilesApp"] >= {"market_profile", "tickvol_profile"}
    assert collected["ReplayCatalogApp"] >= {"catalog"}
    assert collected["ReplayIntradayApp"] >= {"intraday"}


# --------------------------------------------------------------------------------------
# 4. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("modules_requested", [2, 6], ids=["parse_2", "parse_6"])
def test_the_source_is_parsed_once_per_module(modules_requested: int) -> None:
    """モジュール 2 本 / 6 本の 2 点で「parse 回数 − モジュール数 = 0」。

    走査のたびに parse し直す（O(n^2)）形になっていないことだけを固定する。
    回数リテラルは焼き込まず、要求したモジュール数から導出する。
    """
    # Arrange
    parsed: "list[int]" = []

    def _spy(source, *args, **kwargs):
        parsed.append(len(source))
        return ast.parse(source, *args, **kwargs)

    targets = _FRAMEWORK_MODULES[:modules_requested]
    # Act
    collected = {m.__name__: _tree_of(m, parse=_spy) for m in targets}
    # Assert
    assert len(collected) == modules_requested
    assert len(parsed) - modules_requested == 0, (
        f"モジュール {modules_requested} 本に対し parse が {len(parsed)} 回発行された"
        "（採取した木を使い回さず作り直して捨てている）"
    )
