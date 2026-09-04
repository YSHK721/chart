"""機能別ルート App の委譲骨格の同一性を AST で固定する（ISSUE-479 Wave2 再レビュー 🟡-2）。

固定する規則:
    5 本のルート App（candles / compute / intraday / profiles / catalog）は、内側 App を
    包んで「自分が持たない属性は内側へ委譲する」という同一の骨格を持つ。その骨格——
    ``inner`` プロパティと ``__getattr__``——の**コードが 5 本で 1 文字も食い違わない**
    ことを AST で突き合わせる。

なぜ基底クラスを抽出しないのか（レビュー提示の代替案の採用理由）:
    重複を消す素直な手は共通基底の抽出だが、それは 5 本に**継承を強制**する。同型の判断は
    本 ISSUE の J-6 で既に下されており（強制継承を棄却）、ここで逆の判断を採ると設計が割れる。
    重複そのものが害なのではなく、**片方だけが書き換わって食い違うこと**が害である。よって
    消すのではなく、食い違いを機械的に検出する。規約は宣言でなく検査で強制する。

なぜ docstring を比較から外すのか（実測に基づく）:
    5 本の ``__getattr__`` のうち ``serve_replay_candles`` だけが長い docstring を持つ
    （「Handler と他のルート App は compute / _heavy_worker / 各 *_enabled を属性で引く」）。
    つまり「完全一致」が成り立つのは**コード**であって散文ではない。散文まで固定すると、
    その 1 本の説明を消す圧力になる（説明を消すのは是正ではない）。畳むのは docstring だけで、
    文・式・引数・デコレータ・注釈はすべて比較対象に残す。

検出力の実測（本ガードが空振りでないことの証拠・2026-09-04）:
    ``serve_replay_intraday`` の ``__getattr__`` へ無害な差異（``probe = inner`` の 1 文を挟む・
    振る舞いは不変）を注入すると **2 failed / 5 passed**——落ちたのは
    ``...shares_the_identical_delegation_skeleton[__getattr__]`` と
    ``...docstring_is_outside_the_comparison``。Edit で戻すと **7 passed**。差異の注入は git の
    破壊的コマンドを使わず Edit で行い、復元後に ``git status`` へ実装差分が残らないことを確認した。
    ＝本ガードは「振る舞いが変わらない片側変更」を検出する（状態検証では落ちない類の差分）。

計算量検定（絶対命令 2026-08-28）: 骨格の採取はモジュール 1 本につき parse 1 回
    （発行 − 使用 = 0）。素朴に書くと突き合わせのたびに parse し直して O(n^2) になるため、
    モジュール数を変えた 2 点で「parse 回数 − モジュール数 = 0」を固定する。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from simulator.replay_ui.framework import (
    serve_replay_candles,
    serve_replay_catalog,
    serve_replay_compute,
    serve_replay_intraday,
    serve_replay_profiles,
)

#: 委譲骨格を共有する 5 本（モジュール, App クラス名）。App を増やしたらここへ 1 行足す。
_ROUTE_APP_MODULES = (
    (serve_replay_candles, "ReplayCandlesApp"),
    (serve_replay_compute, "ReplayComputeApp"),
    (serve_replay_intraday, "ReplayIntradayApp"),
    (serve_replay_profiles, "ReplayProfilesApp"),
    (serve_replay_catalog, "ReplayCatalogApp"),
)

#: 同一であることを要求する骨格メンバー。``__init__`` は App ごとにルート表が違うので**入れない**
#: （そこは同一でなく、同一を要求すると誤検出になる）。
_SKELETON_MEMBERS = ("inner", "__getattr__")


def _module_path(module) -> Path:
    src = inspect.getsourcefile(module)
    assert src is not None, f"ソースファイルが取れない: {module!r}"
    return Path(src)


def _without_docstring(body: "list[ast.stmt]") -> "list[ast.stmt]":
    """先頭の docstring 文だけを落とした本体を返す（parse し直さない）。"""
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _normalize(fn: ast.FunctionDef) -> str:
    """比較用の正規形。docstring だけを外し、他（引数・注釈・デコレータ・文）は残す。"""
    fn.body = _without_docstring(fn.body)
    assert fn.body, f"docstring を外すと本体が空になる: {fn.name}"
    return ast.dump(fn)


def _skeleton_of(module, class_name: str, *, parse=ast.parse) -> "dict[str, str]":
    """1 モジュールを **1 回だけ** parse し、骨格メンバーの正規化 AST を返す。"""
    path = _module_path(module)
    tree = parse(path.read_text(encoding="utf-8"))
    klass = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name),
        None,
    )
    assert klass is not None, f"クラスが見つからない: {class_name} in {path}"
    found = {
        m.name: m
        for m in klass.body
        if isinstance(m, ast.FunctionDef) and m.name in _SKELETON_MEMBERS
    }
    missing = [m for m in _SKELETON_MEMBERS if m not in found]
    assert not missing, f"{class_name} に骨格メンバーが無い: {missing}"
    return {name: _normalize(fn) for name, fn in found.items()}


@pytest.fixture(scope="module")
def skeletons() -> "dict[str, dict[str, str]]":
    return {
        cls: _skeleton_of(mod, cls) for mod, cls in _ROUTE_APP_MODULES
    }


# --------------------------------------------------------------------------------------
# 1. 骨格の同一性
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("member", _SKELETON_MEMBERS)
def test_every_route_app_shares_the_identical_delegation_skeleton(skeletons, member) -> None:
    """5 本の ``inner`` / ``__getattr__`` のコードが 1 つの実体へ畳まれる。

    片方だけが書き換わると、委譲が App ごとに食い違って「受け口はあるのに結線が死ぬ」
    （ISSUE-291 の形）が 1 本だけで起きる。差分は読み手に見える形で提示する。
    """
    # Arrange
    reference_class, *others = [cls for _, cls in _ROUTE_APP_MODULES]
    reference = skeletons[reference_class][member]
    # Act
    diverged = [cls for cls in others if skeletons[cls][member] != reference]
    # Assert
    assert diverged == [], (
        f"{member} が {reference_class} と食い違う App: {diverged}。"
        " 5 本は同一の委譲骨格を持つ規約である（片側だけの変更を通さない）。"
    )


def test_the_skeleton_extractor_is_not_vacuous(skeletons) -> None:
    """抽出器の空振り検出: 5 本ぶん・骨格 2 面が実際に採れている。

    抽出に失敗して空の集合どうしを比べると、上のテストは常に緑になる（ガードが死ぬ）。
    """
    # Arrange / Act
    classes = [cls for _, cls in _ROUTE_APP_MODULES]
    # Assert
    assert sorted(skeletons) == sorted(classes)
    for cls in classes:
        assert sorted(skeletons[cls]) == sorted(_SKELETON_MEMBERS), cls
        for member in _SKELETON_MEMBERS:
            assert skeletons[cls][member], f"{cls}.{member} の正規化結果が空"


def test_the_skeleton_comparison_can_see_a_difference(skeletons) -> None:
    """比較器の検出力: 1 文の違いを別物として見る（同一性の主張が空虚でないこと）。

    上の同一性テストは「差が無い」ことを主張する。差を見せたときに落ちる比較器で
    測っていることを、ここで自己検定する（変異注入の常設版）。
    """
    # Arrange: 実物の __getattr__ に 1 文だけ足した変異体。
    reference_class = _ROUTE_APP_MODULES[0][1]
    reference = skeletons[reference_class]["__getattr__"]
    mutated_src = (
        "def __getattr__(self, name: str) -> Any:\n"
        '    inner = self.__dict__.get("_inner")\n'
        "    if inner is None:\n"
        "        raise AttributeError(name)\n"
        "    probe = inner\n"          # ← 無害だが骨格としては別物
        "    return getattr(probe, name)\n"
    )
    mutated_fn = ast.parse(mutated_src).body[0]
    assert isinstance(mutated_fn, ast.FunctionDef)
    # Act
    mutated = _normalize(mutated_fn)
    # Assert
    assert mutated != reference, "比較器が別物の骨格を同一とみなしている（ガードが空虚）"


def test_the_docstring_is_outside_the_comparison(skeletons) -> None:
    """散文の差は比較対象外（実測: candles の ``__getattr__`` だけ docstring が長い）。

    この前提が崩れる（5 本の docstring が揃う）と、上の「docstring を外す」という
    設計判断の根拠が消える。根拠が消えたことに気付けるよう、前提そのものを固定する。
    """
    # Arrange
    docstrings = {}
    for mod, cls in _ROUTE_APP_MODULES:
        tree = ast.parse(_module_path(mod).read_text(encoding="utf-8"))
        klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
        fn = next(m for m in klass.body if isinstance(m, ast.FunctionDef) and m.name == "__getattr__")
        docstrings[cls] = ast.get_docstring(fn)
    # Act / Assert: 散文は割れているのに、骨格（比較対象）は割れていない。
    assert len(set(docstrings.values())) > 1, (
        "5 本の __getattr__ docstring が揃った。docstring を比較から外す理由"
        " （散文の差を是正圧力にしない）が消えたので、本モジュールの設計判断を見直すこと。"
    )
    reference = skeletons[_ROUTE_APP_MODULES[0][1]]["__getattr__"]
    assert all(s["__getattr__"] == reference for s in skeletons.values())


# --------------------------------------------------------------------------------------
# 2. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("modules_requested", [2, 5], ids=["parse_2", "parse_5"])
def test_the_skeleton_is_parsed_once_per_module(modules_requested: int) -> None:
    """モジュール 2 本 / 5 本の 2 点で「parse 回数 − モジュール数 = 0」。

    突き合わせのたびに parse し直す（O(n^2)）形になっていないことだけを固定する。
    回数リテラルは焼き込まず、要求したモジュール数から導出する。
    """
    # Arrange
    parsed: "list[int]" = []

    def _spy(source, *args, **kwargs):
        parsed.append(len(source))
        return ast.parse(source, *args, **kwargs)

    targets = _ROUTE_APP_MODULES[:modules_requested]
    # Act
    skeletons = {cls: _skeleton_of(mod, cls, parse=_spy) for mod, cls in targets}
    # Assert
    assert len(skeletons) == modules_requested
    assert len(parsed) - modules_requested == 0, (
        f"モジュール {modules_requested} 本に対し parse が {len(parsed)} 回発行された"
        "（採取した木を使い回さず作り直して捨てている）"
    )
