"""rebuild が 1 日分の素材化を手書き複製せず権威の口で呼ぶ（ISSUE-511 段階 3 前提 (c)）。

用語（初出定義）:
    素材化
        ＝ 1 日分のティックを CSV に書く M1 行へ変える順序（分へ畳む → 外れ分除去 → 行選択 →
          残った分だけ気配幅）。順序の唯一源は ``marketdata.tick_m1`` の素材化関数であり、公開の口は
          :func:`marketdata.tick_m1.materialize_m1_day`。
    手書き複製
        ＝ ``rebuild.authoritative_day_m1`` が ``tick_m1.ticks_to_m1`` → ``marketdata.outlier_policy.repair_day_outliers`` を自分で並べていた形。
          point を付けると外れ分の気配幅を計算してから捨てる（R-1 と同型）。
    宣言
        ＝ 台帳記述子の ``spread_point_snapshot``（None＝spread 列を持たない系列）。

本検定が固定するもの:
  1. 畳みの浪費ゼロ: 畳んだティック数 − 当日 parquet のティック数 = 0（2 回目の畳み・他日の畳みの不在）。
     数える点は唯一の畳み点 ``marketdata.tick_m1._fold_ticks``（公開の口の入口ではない＝委譲先の
     内部での 2 回目も捕まえる）。外れ分の OHLC は日中央値の母集団として使うので「使用」に数える。規模 2 点。
  2. 気配幅の浪費ゼロ（``authoritative_day_m1``）: 発行した分 − 出力行数 = 0・入力ティック − 残った分の
     ティック = 0・外れ分は発行に含まれない。規模 2 点。
  3. ``rebuild_day`` 経路でも気配幅の浪費ゼロ・権威と一致して UNCHANGED。規模 2 点。
  4. 構造: rebuild.py は ``tick_m1.ticks_to_m1`` を呼ばず ``tick_m1.materialize_m1_day`` を呼ぶ
     （属性呼出・別名・from import した関数の直接呼出の別を問わない）。
  Guard: rebuild.py は tick_m1 の private 名を参照しない（属性参照・from import・別名のいずれでも）／
         宣言無しでは気配幅を 1 つも計算しない。
  回数そのもの（N 回）は期待値にしない（固定するのは無駄の不在）。

書込・tick 木はすべて tmp_path（data_dir を必ず渡す）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pandas as pd
import pytest

from marketdata import quote_spread, tick_m1
from marketdata import symbol_spec_snapshot as sss
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor
from marketdata.mt5_ticks import rebuild

_REBUILD_SOURCE = Path(rebuild.__file__)
_TREE = "RBM225"  # tick 木の枝（テスト専用・tmp_path の中だけ）
_PAIR = (sss.OANDA_JAPAN_MT5_LIVE, "JP225")  # 宣言する組（実在するスナップショット）
_DECLARED = "zz_rebuild_spread_declared"
_UNDECLARED_REF = "jp225_mt5"  # 台帳に在り宣言の無い ref（本番の rebuild 対象）
_DAY0 = pd.Timestamp("2026-09-01")
#: 外れ分の水準（通常 ~66,000 に対し -77%＝outlier_policy の ±30% 規則で確実に除去される）。
_PHANTOM = 15100.0


@pytest.fixture(autouse=True)
def _cleared_point_cache():
    """point のキャッシュをテスト間で持ち越さない（F.I.R.S.T の Independent）。"""
    _clear_point_cache()
    yield
    _clear_point_cache()


def _clear_point_cache() -> None:
    module = sys.modules.get("marketdata.spread_point")
    if module is not None:
        module._point_size_of_snapshot.cache_clear()


def _register_declared(monkeypatch, tmp_path: Path) -> None:
    """宣言付きの合成ティック ref を台帳へ一時登録する（test_tick_m1_spread_schema_guard の作法）。"""
    monkeypatch.setitem(REGISTRY, _DECLARED, DatasetDescriptor(
        path=tmp_path / f"{_DECLARED}_m1.csv", symbol="JP225", tick=True,
        price_basis="bid", vendor="mt5", spread_point_snapshot=_PAIR,
    ))


def _day(k: int) -> pd.Timestamp:
    return _DAY0 + pd.Timedelta(days=k)


def _put_day(
    data_dir: Path, day: pd.Timestamp, *, minutes: int, per_minute: int, phantom=()
) -> pd.DataFrame:
    """``day`` 09:00 から ``minutes`` 分 × ``per_minute`` 本のティックを tick 木へ置いて返す。

    ``phantom`` に挙げた分だけ外れ水準（ISSUE-107 と同型の配信欠損ファントム）にする。
    """
    start = day + pd.Timedelta(hours=9)
    step_ms = 60_000 // per_minute
    stamps, bids, asks = [], [], []
    for m in range(minutes):
        for i in range(per_minute):
            bid = _PHANTOM if m in phantom else 66000.0 + m * 2.0 + i * 0.1
            stamps.append(start + pd.Timedelta(minutes=m, milliseconds=i * step_ms))
            bids.append(bid)
            asks.append(bid + 7.0 + (i % 3) * 0.1)
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime(stamps).tz_localize("UTC"),
        "bidPrice": bids,
        "askPrice": asks,
    })
    p = tick_m1.day_parquet_path(day, symbol=_TREE, data_dir=data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(p)
    return frame


def _put_stored_days(
    data_dir: Path, stored_days: int, *, minutes: int, per_minute: int, phantom
) -> "tuple[pd.Timestamp, pd.DataFrame]":
    """``stored_days`` 日ぶんを置き、最後の日（再構築の対象日・外れ分あり）とそのティックを返す。"""
    target = _day(stored_days - 1)
    ticks = None
    for k in range(stored_days):
        day = _day(k)
        frame = _put_day(
            data_dir, day, minutes=minutes, per_minute=per_minute,
            phantom=phantom if day == target else (),
        )
        if day == target:
            ticks = frame
    return target, ticks


def _build_csv(data_dir: Path, ref: str, stored_days: int) -> Path:
    """権威（全量経路）で M1 CSV を作る（rebuild の置換対象が既に在る状態）。"""
    # 価格基準は渡さない（2 つの ref はどちらも台帳に登録済み＝台帳が唯一の源・段階 6・V-3）。
    return tick_m1.build_m1_from_ticks(
        _day(0), _day(stored_days - 1), symbol=_TREE, ref=ref, data_dir=data_dir,
    )


def _minute_of(ticks: pd.DataFrame) -> pd.Series:
    return ticks["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None).dt.floor("min")


def _spy_folds(monkeypatch) -> "list[int]":
    """唯一の畳み点 ``marketdata.tick_m1._fold_ticks`` を包み、畳んだティック数を記録する。

    公開の口（``marketdata.tick_m1.materialize_m1_day`` / ``marketdata.tick_m1.ticks_to_m1``）の入口ではなく
    両者の共通前段を数える。入口で数えると、委譲先の内部で 2 回畳む変異（素材化関数の中で畳みを
    2 回呼ぶ）が 1 回に見えて素通りする（レビュー指摘 Y-1）。どちらの口も畳みをモジュール属性として
    引くので、属性の差し替えで全経路の畳みを捕まえる。
    """
    folded: "list[int]" = []
    real = tick_m1._fold_ticks

    def spy(ticks, *args, **kwargs):
        folded.append(len(ticks))
        return real(ticks, *args, **kwargs)

    monkeypatch.setattr(tick_m1, "_fold_ticks", spy)
    return folded


def _spy_spread(monkeypatch) -> "list[tuple[int, int, frozenset]]":
    """``quote_spread.minute_spread_points`` を包み、発行ごとに (入力ティック数, 出力分数, 分の集合) を記録。"""
    real = quote_spread.minute_spread_points
    calls: "list[tuple[int, int, frozenset]]" = []

    def spy(bid, ask, minute, **kwargs):
        out = real(bid, ask, minute, **kwargs)
        calls.append((len(bid), len(out), frozenset(pd.DatetimeIndex(minute.unique()))))
        return out

    monkeypatch.setattr(quote_spread, "minute_spread_points", spy)
    return calls


# =====================================================================
# 1. 畳みの浪費ゼロ（2 回目の畳み・他日の畳みの不在）
# =====================================================================

_FOLD_POINTS = [
    pytest.param(20, 2, id="20perMin-2days"),
    pytest.param(80, 5, id="80perMin-5days"),
]


@pytest.mark.parametrize("per_minute,stored_days", _FOLD_POINTS)
def test_authoritative_day_folds_only_the_ticks_of_the_day_once(
    tmp_path, monkeypatch, per_minute, stored_days
):
    """CX: ``authoritative_day_m1`` が畳んだティック数 − 当日 parquet のティック数 = 0。"""
    # Arrange
    day, ticks = _put_stored_days(
        tmp_path, stored_days, minutes=6, per_minute=per_minute, phantom=(2,)
    )
    folded = _spy_folds(monkeypatch)

    # Act
    got = rebuild.authoritative_day_m1(day, symbol=_TREE, ref=_UNDECLARED_REF, data_dir=tmp_path)

    # Assert: 外れ分も日中央値の母集団として使う＝当日の全ティックが使用。
    used = len(ticks)
    assert len(got) > 0 and used > 0  # 空振り防止
    assert sum(folded) - used == 0, f"畳み {folded} − 当日ティック {used} ≠ 0"


@pytest.mark.parametrize("per_minute,stored_days", _FOLD_POINTS)
def test_rebuild_day_folds_only_the_ticks_of_the_day_once(
    tmp_path, monkeypatch, per_minute, stored_days
):
    """CX: ``rebuild_day`` が畳んだティック数 − 当日 parquet のティック数 = 0（保存日数に比例しない）。"""
    # Arrange
    day, ticks = _put_stored_days(
        tmp_path, stored_days, minutes=6, per_minute=per_minute, phantom=(2,)
    )
    _build_csv(tmp_path, _UNDECLARED_REF, stored_days)
    folded = _spy_folds(monkeypatch)

    # Act
    rebuild.rebuild_day(
        day, symbol=_TREE, ref=_UNDECLARED_REF, data_dir=tmp_path, update_rollups=False
    )

    # Assert
    used = len(ticks)
    assert used > 0  # 空振り防止
    assert sum(folded) - used == 0, f"畳み {folded} − 当日ティック {used} ≠ 0"


# =====================================================================
# 2. 気配幅の浪費ゼロ（authoritative_day_m1・宣言付き ref）
# =====================================================================

@pytest.mark.parametrize("minutes,phantom", [
    pytest.param(10, (3, 7), id="10min-2phantom"),
    pytest.param(40, (1, 5, 9, 13, 17, 21, 25, 29), id="40min-8phantom"),
])
def test_authoritative_day_computes_spreads_only_for_the_surviving_minutes(
    tmp_path, monkeypatch, minutes, phantom
):
    """CX: 発行した分 − 出力行数 = 0／入力ティック − 残った分のティック = 0／外れ分は発行に無い。"""
    # Arrange
    _register_declared(monkeypatch, tmp_path)
    day, ticks = _put_stored_days(tmp_path, 1, minutes=minutes, per_minute=3, phantom=phantom)
    calls = _spy_spread(monkeypatch)

    # Act
    expected = rebuild.authoritative_day_m1(day, symbol=_TREE, ref=_DECLARED, data_dir=tmp_path)

    # Assert
    phantom_minutes = {day + pd.Timedelta(hours=9, minutes=m) for m in phantom}
    surviving_ticks = int(_minute_of(ticks).isin(expected.index).sum())
    issued_minutes = frozenset().union(*(c[2] for c in calls))
    assert "spread" in expected.columns, f"宣言付き ref の権威 M1 に spread 列がありません: {list(expected.columns)}"
    assert len(expected) == minutes - len(phantom)  # 前提: 外れ分は除去されている
    assert sum(c[1] for c in calls) - len(expected) == 0
    assert sum(c[0] for c in calls) - surviving_ticks == 0
    assert not (issued_minutes & phantom_minutes), "外れ分の気配幅を計算しています"


# =====================================================================
# 3. rebuild_day 経路でも気配幅の浪費ゼロ
# =====================================================================

@pytest.mark.parametrize("stored_days", [2, 5])
def test_rebuild_day_of_a_declared_series_computes_spreads_only_for_the_day_rows(
    tmp_path, monkeypatch, stored_days
):
    """CX: 権威で作った spread 付き CSV の外れ値日を作り直すと UNCHANGED かつ 発行 − 当日行数 = 0。"""
    # Arrange
    _register_declared(monkeypatch, tmp_path)
    day, _ = _put_stored_days(tmp_path, stored_days, minutes=10, per_minute=3, phantom=(3, 7))
    path = _build_csv(tmp_path, _DECLARED, stored_days)
    written = pd.read_csv(path)
    day_rows = int((pd.to_datetime(written["date"]).dt.normalize() == day).sum())
    calls = _spy_spread(monkeypatch)

    # Act
    outcome = rebuild.rebuild_day(
        day, symbol=_TREE, ref=_DECLARED, data_dir=tmp_path, update_rollups=False
    )

    # Assert
    assert "spread" in written.columns and day_rows > 0  # 前提（空振り防止）
    assert outcome == rebuild.UNCHANGED
    assert sum(c[1] for c in calls) - day_rows == 0


# =====================================================================
# rebuild.py の中で tick_m1 を指す名前の解決（4. 構造と Guard が共有する唯一の判定）
# =====================================================================

_TICK_M1_MODULE = "marketdata.tick_m1"
_REBUILD_PACKAGE = "marketdata.mt5_ticks"  # rebuild.py の所属パッケージ（相対 import の起点）


def _absolute_module(node: ast.ImportFrom) -> str:
    """from import の取り込み元を rebuild.py の所属パッケージから絶対モジュール名へ直す。"""
    parts = _REBUILD_PACKAGE.split(".")
    base = parts[: len(parts) - node.level + 1] if node.level else []
    return ".".join([*base, *([node.module] if node.module else [])])


def _dotted(expr: ast.expr) -> "str | None":
    """Name / Attribute の連鎖を a.b.c の文字列にする（それ以外は None）。"""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        head = _dotted(expr.value)
        return None if head is None else f"{head}.{expr.attr}"
    return None


def _tick_m1_bindings(tree: ast.AST) -> "set[str]":
    """tick_m1 モジュールそのものを指す名前（既定の名前 tick_m1・完全修飾名・import 別名・
    from import で取り込んだモジュール名とその別名（相対 import を含む））。"""
    bound = {"tick_m1", _TICK_M1_MODULE}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound |= {a.asname for a in node.names if a.name == _TICK_M1_MODULE and a.asname}
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_module(node)
            bound |= {a.asname or a.name for a in node.names if f"{module}.{a.name}" == _TICK_M1_MODULE}
    return bound


def _tick_m1_imports(tree: ast.AST) -> "list[tuple[str, str]]":
    """tick_m1 から from import で取り込んだ名前の組（局所名, tick_m1 での名前）を取り込み 1 件ずつ列挙する。

    as による改名と相対 import を含む。同じ局所名への再取り込みも 1 件ずつ残す（畳まない）。
    """
    return [
        (a.asname or a.name, a.name) for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and _absolute_module(node) == _TICK_M1_MODULE
        for a in node.names
    ]


# =====================================================================
# 4. 構造: 手書き複製の撤去
# =====================================================================

def _tick_m1_function_of(func: ast.expr, bound: "set[str]", imported: "dict[str, set[str]]") -> "set[str]":
    """呼出先がなりうる tick_m1 の関数名の集合（tick_m1 を指す名前の属性・from import した名前の直接呼出）。

    同じ局所名へ再取り込みした名前は、取り込んだ全ての名前のどれにもなりうるとして全部返す（畳まない）。
    """
    if isinstance(func, ast.Attribute) and _dotted(func.value) in bound:
        return {func.attr}
    if isinstance(func, ast.Name):
        return imported.get(func.id, set())
    return set()


def _tick_m1_functions_called() -> "set[str]":
    """rebuild.py が呼ぶ tick_m1 の関数名を、import・from import・完全修飾名の形について（代入による別名や getattr 経由は振る舞いの検定が担う）集める。

    tick_m1 を指す名前の判定は :func:`_tick_m1_bindings` の 1 つだけに従う（既定の名前・完全修飾名・
    import 別名・相対 import）。from import で取り込んだ関数の直接呼出も tick_m1 の関数の呼出に数える。
    """
    tree = ast.parse(_REBUILD_SOURCE.read_text(encoding="utf-8"))
    bound = _tick_m1_bindings(tree)
    imported: "dict[str, set[str]]" = {}
    for local, name in _tick_m1_imports(tree):
        imported.setdefault(local, set()).add(name)
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    return set().union(*(_tick_m1_function_of(func, bound, imported) for func in calls))


def test_rebuild_calls_the_public_materializer_instead_of_folding_by_hand():
    """rebuild.py は ``tick_m1.ticks_to_m1`` を呼ばず、素材化の公開の口を呼ぶ（順序の第 2 定義を持たない）。

    ISSUE-511 段階 8-D-2b の段 3 で rebuild は 1 系列専用の口から**系列の組の口**へ移った
    （当日の parquet を 1 回読み、1 回だけ畳んで組の各系列へ配る）。期待する名前だけを同じ 1 段
    移し、表明（手書き畳みの不在）は 1 文字も変えていない。空振り（名前を 1 つも拾えないまま
    緑になる形）でないことは、拾った名前の集合が空でないことで示す。
    """
    called = _tick_m1_functions_called()
    assert called, "tick_m1 の呼出を 1 つも拾えていません（名前解決が空振りしています）"
    assert "ticks_to_m1" not in called
    assert "materialize_m1_day_for_series" in called


# =====================================================================
# Guard（前後とも緑）
# =====================================================================

def _private_tick_m1_references(tree: ast.AST) -> "list[str]":
    """rebuild.py が tick_m1 の private 名へ届く箇所を、import・from import・完全修飾名の形について（代入による別名や getattr 経由は振る舞いの検定が担う）列挙する。

    拒否する形: 属性参照（tick_m1 の後に _x・別名 t の後に _x・完全修飾名の後に _x）と、
    tick_m1 からの from import で _ 始まりの名前を取り込む形（as による改名・相対 import も含む）。
    from import の形は属性参照を 1 つも作らないので、属性参照だけを見る検定を素通りする（レビュー指摘 Y-1）。
    """
    bound = _tick_m1_bindings(tree)
    attributes = [
        f"{_dotted(node.value)}.{node.attr}" for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr.startswith("_")
        and _dotted(node.value) in bound
    ]
    imported = [
        f"from {_TICK_M1_MODULE} import {name}" for _, name in _tick_m1_imports(tree)
        if name.startswith("_")
    ]
    return sorted(attributes + imported)


def test_rebuild_never_reaches_into_private_attributes_of_tick_m1():
    """Guard: rebuild.py は tick_m1 の private 名を参照しない（公開の口だけを使う・属性参照／from import／別名の別を問わない）。"""
    tree = ast.parse(_REBUILD_SOURCE.read_text(encoding="utf-8"))
    assert _private_tick_m1_references(tree) == []


@pytest.mark.parametrize("stored_days", [2, 5])
def test_rebuild_of_an_undeclared_series_computes_no_spread(tmp_path, monkeypatch, stored_days):
    """Guard/CX: 宣言の無い ref では気配幅を 1 つも計算しない（出力に spread が無い＝使用 0）。"""
    # Arrange
    day, _ = _put_stored_days(tmp_path, stored_days, minutes=10, per_minute=3, phantom=(3, 7))
    path = _build_csv(tmp_path, _UNDECLARED_REF, stored_days)
    calls = _spy_spread(monkeypatch)

    # Act
    outcome = rebuild.rebuild_day(
        day, symbol=_TREE, ref=_UNDECLARED_REF, data_dir=tmp_path, update_rollups=False
    )

    # Assert
    used = 0
    assert outcome == rebuild.UNCHANGED
    assert "spread" not in pd.read_csv(path, nrows=0).columns
    assert sum(c[1] for c in calls) - used == 0
