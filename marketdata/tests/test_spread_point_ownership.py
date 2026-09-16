"""台帳への直の到達を spread_point の公開面へ閉じる（ISSUE-511 段階 3 の段階 1・V-1）。

用語（初出定義）:
    内部表
        ＝ `marketdata.dataset_registry.REGISTRY`（可変 dict）。照会の口ではなく格納物そのもの。
    宣言欄の照会
        ＝ `marketdata.dataset_registry.spread_point_snapshot_of`。記述子の欄
          ``spread_point_snapshot`` を引く台帳側の口。
    所有の照会
        ＝ `marketdata.spread_point.ledger_owns_point`。台帳がこの ref の point を持つか
          （＝呼出側から point を受けない ref か）の真偽。
    遅延の解決口
        ＝ `marketdata.spread_point.declared_point_resolver`。宣言があれば「呼び出すと point を
          返す callable」、宣言が無ければ ``None``。
    宣言の照会
        ＝ `marketdata.spread_point.declared_snapshot_of`。宣言そのもの（スナップショットの所在）。
          食い違いを報せる文面を組む素材化側が使う。

本検定が固定するもの:
  S-1 所有の照会は登録済み ref と台帳外 ref を区別し、スナップショットを読まない。
  S-2 解決口は宣言のある ref で point を返し、宣言の無い登録済み ref と台帳外 ref では ``None``。
  S-3 解決は遅延（取得しただけでは読まない）であり、同じ組を繰り返し解決しても読込は増えない。
  R-2 `marketdata.tick_m1` は台帳へ直に届かない（内部表・宣言欄の照会のいずれも AST で 0 件。
      2 記号 × import 4 形式の負の対照で検出力を示す。対照が名指す 2 記号は走査の定数から導出せず
      literal で置く＝射程を狭めたら対照は消えるのではなく落ちる）。
  CX-A 継ぎ目 `marketdata.symbol_spec_snapshot.load_snapshot` と
       `marketdata.quote_spread.minute_spread_points`:
       スナップショット読込 − spread に使った読込 = 0（追記 2 回と 20 回の 2 点で、発行は増えない）。
  CX-B 継ぎ目 `marketdata.dataset_registry.spread_point_snapshot_of`:
       台帳照会 − 解決口の取得 = 0（build の日数 1 と 10 の 2 点で、発行は増えない）。

本検定が固定しないもの（射程の明示）:
  - 登録済み ref への point 明示の拒否は `marketdata/tests/test_tick_m1_spread_schema_guard.py` の
    test_an_explicit_point_for_a_registered_ref_is_refused_before_any_io が固定する（実台帳の
    2 ref と合成 ref × build/append の 6 通り）。ここに置いていた同主旨の 2 通りは、その弱い部分集合
    （変異させると両方が同時に落ちる＝固有の検出力が無い）だったため削除した。
  - R-2 の AST 走査が見るのは**構文木に現れる名前**だけである。``getattr`` のような文字列経由の
    到達は検出しない。

計算量の規約（絶対命令 2026-08-28）: 回数そのものは期待値に焼き込まない。固定するのは
「発行 − 使用 = 0」と「入力の規模を変えても発行が増えないこと」だけである。

合成系列の組み立て（tick 木・書き手呼出・Test Spy）は `marketdata/tests/spread_series_fixture.py`
が唯一源。書込はすべて ``tmp_path``。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from marketdata import dataset_registry, quote_spread, tick_m1
from marketdata import spread_point as sp
from marketdata import symbol_spec_snapshot as sss
from spread_series_fixture import (
    SNAPSHOT_PAIR,
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    day,
    header_of,
    put_day,
    put_days,
    register_tick_ref,
    run_writer,
    snapshot_point,
    spy,
    spy_snapshot_reads,
    spy_spent_points,
    used_reads,
)

_DECLARED = "zz_own_declared"
_UNDECLARED = "zz_own_undeclared"
_UNREGISTERED = "zz_own_unregistered"  # 台帳に無い ref


# =====================================================================
# S-1. 所有の照会（台帳が point を持つ ref か）
# =====================================================================
def test_the_ledger_owns_the_point_of_a_registered_ref(tmp_path, monkeypatch):
    """登録済みの ref（宣言の有無に依らない）は台帳が point を持つ側である。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    register_tick_ref(monkeypatch, tmp_path, _UNDECLARED, None)

    # Act / Assert
    assert sp.ledger_owns_point(_DECLARED) is True
    assert sp.ledger_owns_point(_UNDECLARED) is True


def test_the_ledger_does_not_own_the_point_of_an_unregistered_ref(monkeypatch):
    """台帳に無い ref は台帳が point を持たない（呼出側の point が効く側）。スナップショットも読まない。"""
    # Arrange
    reads = spy(monkeypatch, sss, "load_snapshot")

    # Act
    owned = sp.ledger_owns_point(_UNREGISTERED)

    # Assert
    used = 0  # point を 1 つも返していない
    assert owned is False
    assert len(reads) - used == 0


def test_asking_who_owns_the_point_reads_no_snapshot(tmp_path, monkeypatch):
    """所有の照会は台帳の照会であって値の解決ではない（登録済みでもスナップショットを読まない）。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    reads = spy(monkeypatch, sss, "load_snapshot")

    # Act
    owned = sp.ledger_owns_point(_DECLARED)

    # Assert
    used = 0
    assert owned is True
    assert len(reads) - used == 0


# =====================================================================
# S-2. 遅延の解決口と宣言の照会
# =====================================================================
def test_the_resolver_of_a_declared_ref_returns_the_snapshot_point(tmp_path, monkeypatch):
    """宣言のある ref の解決口は、呼ぶとスナップショットの point_size を返す。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)

    # Act
    resolver = sp.declared_point_resolver(_DECLARED)

    # Assert
    assert resolver is not None
    assert resolver() == snapshot_point()


@pytest.mark.parametrize("ref", [_UNDECLARED, _UNREGISTERED], ids=["registered_none", "unregistered"])
def test_an_undeclared_ref_has_no_resolver_and_reads_no_snapshot(tmp_path, monkeypatch, ref):
    """宣言の無い登録済み ref と台帳外 ref は解決口を持たない。スナップショットも読まない。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _UNDECLARED, None)
    reads = spy(monkeypatch, sss, "load_snapshot")

    # Act
    resolver = sp.declared_point_resolver(ref)

    # Assert
    used = 0  # point を 1 つも返していない
    assert resolver is None
    assert len(reads) - used == 0


def test_the_declared_snapshot_is_answered_without_reading_it(tmp_path, monkeypatch):
    """宣言の照会は宣言そのもの（所在）を返し、スナップショットは読まない（宣言無しは ``None``）。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    register_tick_ref(monkeypatch, tmp_path, _UNDECLARED, None)
    reads = spy(monkeypatch, sss, "load_snapshot")

    # Act
    declared = sp.declared_snapshot_of(_DECLARED)

    # Assert
    used = 0  # point を 1 つも解決していない
    assert declared == SNAPSHOT_PAIR
    assert sp.declared_snapshot_of(_UNDECLARED) is None
    assert sp.declared_snapshot_of(_UNREGISTERED) is None
    assert len(reads) - used == 0


# =====================================================================
# S-3. 解決は遅延で、繰り返しても読込が増えない
# =====================================================================
def test_taking_the_resolver_does_not_read_the_snapshot_yet(tmp_path, monkeypatch):
    """CX: 解決口を取得しただけでは読まない（使う分が無い周期で読んだら浪費）。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    reads = spy(monkeypatch, sss, "load_snapshot")

    # Act
    resolver = sp.declared_point_resolver(_DECLARED)

    # Assert
    used = 0  # まだ point を使っていない
    assert resolver is not None
    assert len(reads) - used == 0


def test_calling_one_resolver_repeatedly_does_not_add_reads(tmp_path, monkeypatch):
    """CX（規模 2 点）: 同じ解決口を 2 回呼んだときと 20 回呼んだときで読込が等しく、値も等しい。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    issued_by_scale = []
    for n_calls in (2, 20):
        sp.forget_resolved_points()
        reads = spy(monkeypatch, sss, "load_snapshot")

        # Act
        resolver = sp.declared_point_resolver(_DECLARED)
        values = [resolver() for _ in range(n_calls)]

        # Assert
        assert set(values) == {snapshot_point()}
        issued_by_scale.append(len(reads))

    assert issued_by_scale[0] == issued_by_scale[1], (
        f"同じ解決口の呼出を 2 → 20 回にしたらスナップショット読込が {issued_by_scale[0]} →"
        f" {issued_by_scale[1]} へ増えました（呼出ごとに読み直しています）。"
    )


def test_taking_the_resolver_again_does_not_add_reads(tmp_path, monkeypatch):
    """CX（規模 2 点）: 解決口を取り直して解決しても、読込は組の数だけ（呼び口ごとに読み直さない）。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _DECLARED, SNAPSHOT_PAIR)
    issued_by_scale = []
    for n_resolvers in (2, 20):
        sp.forget_resolved_points()
        reads = spy(monkeypatch, sss, "load_snapshot")

        # Act
        values = [sp.declared_point_resolver(_DECLARED)() for _ in range(n_resolvers)]

        # Assert
        assert set(values) == {snapshot_point()}
        issued_by_scale.append(len(reads))

    assert issued_by_scale[0] == issued_by_scale[1], (
        f"解決口の取り直しを 2 → 20 回にしたらスナップショット読込が {issued_by_scale[0]} →"
        f" {issued_by_scale[1]} へ増えました（呼び口を作るたびに読み直しています）。"
    )


# =====================================================================
# R-2. tick_m1 は台帳へ直に届かない（AST）
# =====================================================================
#: 台帳へ直に届く名前。内部表そのものと、宣言欄を引く台帳側の口の 2 つ。
_LEDGER_DIRECT = ("REGISTRY", "spread_point_snapshot_of")


def reaches_the_ledger_directly(tree: ast.AST) -> "list[str]":
    """台帳へ直に届いている箇所を挙げる（空なら合格）。

    数えるのは 2 種類の到達である: 内部表 ``REGISTRY`` そのものと、宣言欄の照会
    spread_point_snapshot_of（台帳 dataset_registry 側の口）。import 形式を変えた迂回
    （属性参照 / 直 import / モジュール別名 / 名前の別名）をいずれも数える。負の対照
    :func:`test_the_ledger_scan_flags_every_import_form` が 2 記号 × 4 形式で検出力を実証する。

    見るのは構文木に現れる名前だけである。``getattr`` のような文字列経由の到達は名前として
    現れないため検出しない（本走査の射程外）。
    """
    found: "list[str]" = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name.split(".")[-1] in _LEDGER_DIRECT:
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.Attribute) and node.attr in _LEDGER_DIRECT:
            found.append(f"attribute .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _LEDGER_DIRECT:
            found.append(f"name {node.id}")
    return found


def test_tick_m1_does_not_reach_the_ledger_directly():
    """``tick_m1`` から内部表・宣言欄の照会への到達は 0 件（どちらも spread_point の公開面が答える）。"""
    # Arrange
    tree = ast.parse(Path(tick_m1.__file__).read_text(encoding="utf-8"))

    # Act / Assert
    assert reaches_the_ledger_directly(tree) == []


#: 負の対照が名指す記号。走査の定数 :data:`_LEDGER_DIRECT` から**導出しない**。導出すると射程を
#: 狭めたときに対照そのものが消え、「検定が 4 件減っただけ」で緑になる（実測: 射程を ``REGISTRY``
#: だけへ戻す変異を掛けたところ、導出型の対照は 1 件も落ちず収集数が 4 件減っただけだった）。
_MUST_BE_FLAGGED = ("REGISTRY", "spread_point_snapshot_of")


@pytest.mark.parametrize("name", list(_MUST_BE_FLAGGED), ids=list(_MUST_BE_FLAGGED))
@pytest.mark.parametrize(
    "template",
    [
        "from marketdata import dataset_registry\nx = dataset_registry.{name}\n",
        "from marketdata.dataset_registry import {name}\nx = {name}\n",
        "import marketdata.dataset_registry as dr\nx = dr.{name}\n",
        "from marketdata.dataset_registry import {name} as R\nx = R\n",
    ],
    ids=["module_attribute", "direct_import", "aliased_module", "aliased_name"],
)
def test_the_ledger_scan_flags_every_import_form(template, name):
    """負の対照（検出力）: 2 記号 × import 4 形式のいずれでも到達は挙がる（文字列一致では無く構文木）。"""
    # Arrange / Act / Assert
    assert reaches_the_ledger_directly(ast.parse(template.format(name=name))) != []


def test_the_ledger_scan_flags_the_module_that_does_reach_the_ledger():
    """正の対照（実ファイル）: 台帳を引く当人（spread_point）では 2 種類とも挙がる。"""
    # Arrange
    tree = ast.parse(Path(sp.__file__).read_text(encoding="utf-8"))

    # Act
    found = reaches_the_ledger_directly(tree)

    # Assert
    assert [f for f in found if "REGISTRY" in f] != []
    assert [f for f in found if "spread_point_snapshot_of" in f] != []


def test_the_ledger_scan_ignores_an_unrelated_module():
    """正の対照: 台帳へ到達しないモジュールは挙げない（偽陽性を作らない）。"""
    # Arrange / Act / Assert
    assert reaches_the_ledger_directly(ast.parse(Path(quote_spread.__file__).read_text("utf-8"))) == []


def test_tick_m1_defines_no_lazy_point_class():
    """遅延解決の実体は spread_point 側に在る（``tick_m1`` に第 2 の定義を残さない）。"""
    # Arrange
    tree = ast.parse(Path(tick_m1.__file__).read_text(encoding="utf-8"))

    # Act
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

    # Assert
    assert [n for n in classes if "LazyPoint" in n] == []


# =====================================================================
# CX-A. 継ぎ目 load_snapshot × minute_spread_points（読込 − 使用 = 0・追記 2 回と 20 回）
# =====================================================================
def test_cx_a_the_snapshot_is_read_through_the_resolver_only_for_the_point_that_is_spent(
    tmp_path, monkeypatch
):
    """CX-A: 追記 2 回と 20 回で 読込 − 使用 = 0、発行は 2 点で等しく、読込は解決口を経て起きる。"""
    reads = spy_snapshot_reads(monkeypatch)
    spent = spy_spent_points(monkeypatch)
    resolvers = spy(monkeypatch, sp, "declared_point_resolver")
    issued_by_scale = []
    for n_calls in (2, 20):
        # Arrange: 初日を build 済み・記憶は空・記録を空にする。
        data_dir = tmp_path / f"calls{n_calls}"
        register_tick_ref(monkeypatch, data_dir, _DECLARED, SNAPSHOT_PAIR)
        put_day(data_dir, day(0))
        out = run_writer(tick_m1.build_m1_from_ticks, _DECLARED, data_dir, day(0), day(0))
        rows_before = len(out.read_text(encoding="utf-8").splitlines())
        sp.forget_resolved_points()
        reads.clear()
        spent.clear()
        resolvers.clear()

        # Act: 1 日ずつ n_calls 回追記する。
        for k in range(1, n_calls + 1):
            put_day(data_dir, day(k))
            run_writer(tick_m1.append_m1_from_ticks, _DECLARED, data_dir, day(0), day(k))

        # Assert
        appended = len(out.read_text(encoding="utf-8").splitlines()) - rows_before
        assert appended > 0  # 空振り防止
        assert header_of(out).endswith(",spread")
        assert len(resolvers) > 0, "spread の point が spread_point の解決口を経ていません。"
        spent_reads = used_reads(reads, spent)
        assert len(reads) - spent_reads == 0, f"{n_calls} 回: 読込 {len(reads)} − 使用 {spent_reads} ≠ 0"
        issued_by_scale.append(len(reads))

    assert issued_by_scale[0] == issued_by_scale[1], (
        f"追記を 2 → 20 回にしたらスナップショット読込が {issued_by_scale[0]} →"
        f" {issued_by_scale[1]} へ増えました（呼出ごとに point を読み直しています）。"
    )


# =====================================================================
# CX-B. 継ぎ目 spread_point_snapshot_of（台帳照会 − 解決口の取得 = 0・日数 1 と 10）
# =====================================================================
def _lookups_and_resolvers(monkeypatch, data_dir: Path, n_days: int) -> "tuple[int, int]":
    """宣言ありの ref を ``n_days`` 日で build したときの（台帳照会, 解決口の取得）の発行数。"""
    register_tick_ref(monkeypatch, data_dir, _DECLARED, SNAPSHOT_PAIR)
    days = put_days(data_dir, n_days)
    lookups = spy(monkeypatch, dataset_registry, "spread_point_snapshot_of")
    resolvers = spy(monkeypatch, sp, "declared_point_resolver")
    out = run_writer(tick_m1.build_m1_from_ticks, _DECLARED, data_dir, days[0], days[-1])
    assert header_of(out).endswith(",spread")  # 空振り防止（spread を実際に書いた）
    return len(lookups), len(resolvers)


def test_cx_b_the_ledger_is_consulted_once_per_resolver_and_does_not_grow_with_days(
    tmp_path, monkeypatch
):
    """CX-B: 台帳照会 − 解決口の取得 = 0（1 日と 10 日の 2 点）。発行は日数で増えない。"""
    # Arrange / Act
    small = _lookups_and_resolvers(monkeypatch, tmp_path / "d1", 1)
    large = _lookups_and_resolvers(monkeypatch, tmp_path / "d10", 10)

    # Assert
    assert small[1] > 0, "解決口を 1 度も取っていません（空振り）。"
    assert small[0] - small[1] == 0, f"1 日: 台帳照会 {small[0]} − 解決口 {small[1]} ≠ 0"
    assert large[0] - large[1] == 0, f"10 日: 台帳照会 {large[0]} − 解決口 {large[1]} ≠ 0"
    assert small == large, f"(台帳照会, 解決口の取得): 1 日 {small} / 10 日 {large}"
