"""spread の point を系列ごとの台帳属性にする（ISSUE-511 段階 3 前提 (a)）— 台帳と読み口の検定。

用語（初出定義）:
    spread の point 宣言
        ＝ 台帳 marketdata.dataset_registry の記述子欄 ``spread_point_snapshot``。値は銘柄仕様
          スナップショットの所在（サーバ名, 銘柄名）＝ load_snapshot の引数そのものであり、point の
          値は持たない。None は spread 列を持たない系列。
    読み口
        ＝ marketdata.spread_point.spread_point_of（ref → point の値。宣言が無ければ None）。

本検定が固定するもの:
  1. 構築時の拒否: tick=False の記述子・形の不正な組は作れない。
  2. 値ピン: 現行台帳の宣言は spread 系列の 1 件のみで、他の記述子はすべて None
     （段階 7a で jp225_mt5_spread が 1 件目の宣言になった。実データはまだ 1 バイトも作っていない）。
  3. 宣言された組のスナップショットが実在する（合成記述子で正・負の対照）。
  4. 読み口はスナップショットの point_size を返す（期待値は表 SYMBOL_FIELD_SOURCES 経由で素の
     JSON から取る・リテラルを書かない）。宣言無し・台帳外は None。スナップショット無しは Fail-Stop。
  5. spread_point.py に MT5 のフィールド名リテラルが無い（AST）。
  6. 銘柄仕様の 1 項目読み（symbol_spec_snapshot.load_symbol_field）は口座属性の表を引かない。
  計算量: 宣言の無い ref ではスナップショットを 1 度も読まない（発行 − 使用 = 0）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import ast
import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

from marketdata import dataset_registry
from marketdata import symbol_spec_snapshot as sss
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor

_SERVER = sss.OANDA_JAPAN_MT5_LIVE
_SYMBOL = "JP225"
# SymbolSpec 側の名前（MT5 のフィールド名ではない）。MT5 名は表 SYMBOL_FIELD_SOURCES からだけ引く。
_POINT_NAME = "point_size"


def _spread_point():
    """被検査モジュール（新設）を実行時に import する（未実装で収集ごと落とさない）。"""
    return importlib.import_module("marketdata.spread_point")


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


@pytest.fixture(scope="module")
def raw() -> dict:
    """スナップショット JSON を**モジュールを経由せず**素で読む（突合の独立性を保つ）。"""
    path = Path(sss.__file__).resolve().parent / "symbol_specs" / _SERVER / f"{_SYMBOL}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_point(raw: dict) -> float:
    """素の JSON から、表 SYMBOL_FIELD_SOURCES が point_size に割り当てたキーの値を引く。"""
    source = sss.SYMBOL_FIELD_SOURCES[_POINT_NAME]
    return source.cast(raw[source.section][source.key])


def _tick_descriptor(tmp_path: Path, **extra) -> DatasetDescriptor:
    """合成のティック記述子（tick=True の必須欄は埋め、残りは ``extra``）。"""
    return DatasetDescriptor(
        path=tmp_path / "zz_m1.csv", symbol=_SYMBOL, tick=True,
        price_basis="bid", vendor="dukascopy", **extra,
    )


def _spy_snapshot_reads(monkeypatch) -> "list[tuple[str, str]]":
    """スナップショット読込（``symbol_spec_snapshot.load_snapshot``）の発行を記録する Test Spy。"""
    real = sss.load_snapshot
    reads: "list[tuple[str, str]]" = []

    def spy(server, symbol):
        reads.append((server, symbol))
        return real(server, symbol)

    monkeypatch.setattr(sss, "load_snapshot", spy)
    return reads


# --------------------------------------------------------------------------- #
# 1. 構築時の拒否
# --------------------------------------------------------------------------- #
def test_a_spread_point_declared_on_a_non_tick_descriptor_is_refused(tmp_path):
    """spread 列はティックから作るので、ティックを持たない記述子は宣言できない。"""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="spread_point_snapshot"):
        DatasetDescriptor(
            path=tmp_path / "x.csv", symbol=_SYMBOL, spread_point_snapshot=(_SERVER, _SYMBOL)
        )


@pytest.mark.parametrize(
    "declared",
    [
        (_SERVER,),
        (_SERVER, _SYMBOL, "extra"),
        ("", _SYMBOL),
        (_SERVER, ""),
        [_SERVER, _SYMBOL],
        f"{_SERVER}/{_SYMBOL}",
        (_SERVER, 225),
    ],
    ids=["one", "three", "empty_server", "empty_symbol", "list", "joined_string", "non_string"],
)
def test_a_malformed_spread_point_declaration_is_refused(tmp_path, declared):
    """宣言は「空でない文字列 2 つ」の tuple だけ（load_snapshot の引数そのもの）。"""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="spread_point_snapshot"):
        _tick_descriptor(tmp_path, spread_point_snapshot=declared)


def test_the_ledger_answers_the_declared_snapshot_and_none_otherwise(tmp_path, monkeypatch):
    """照会: 宣言ありはその組、宣言無しの登録済み ref と台帳外 ref は None（IO なし）。"""
    # Arrange
    monkeypatch.setitem(
        REGISTRY, "zz_declared", _tick_descriptor(tmp_path, spread_point_snapshot=(_SERVER, _SYMBOL))
    )

    # Act
    declared = dataset_registry.spread_point_snapshot_of("zz_declared")
    undeclared = dataset_registry.spread_point_snapshot_of("jp225_tick")
    unregistered = dataset_registry.spread_point_snapshot_of("zz_not_in_ledger")

    # Assert
    assert declared == (_SERVER, _SYMBOL)
    assert undeclared is None
    assert unregistered is None


# --------------------------------------------------------------------------- #
# 2. 値ピン（本段では実データを変えない）
# --------------------------------------------------------------------------- #
def test_only_the_new_spread_series_declares_a_spread_point():
    """宣言を持つのは spread 列つきの新系列だけ（ISSUE-511 段階 3 の段階 7a・ISSUE-533 段階 3）。

    段階 7a より前はここが ``{}``（宣言 0 件）だった。``jp225_mt5_spread`` を足して 1 件になり、
    ISSUE-533 段階 3 の前提工事で Dukascopy 側の対 ``jp225_tick_spread`` を足して 2 件になった。
    **閉じた集合のまま**更新する（``in`` や「含む」へ緩めない。緩めると、宣言が別の既存系列へ
    漏れて既存 CSV の列形が変わっても落ちなくなる＝R-2/Y-2 の再来を検出できない）。
    """
    # Arrange / Act
    declared = {
        ref: d.spread_point_snapshot
        for ref, d in REGISTRY.items()
        if d.spread_point_snapshot is not None
    }

    # Assert
    assert declared == {
        "jp225_tick_spread": (_SERVER, _SYMBOL),
        "jp225_mt5_spread": (_SERVER, _SYMBOL),
    }


# --------------------------------------------------------------------------- #
# 3. 宣言された組のスナップショットが実在する
# --------------------------------------------------------------------------- #
def snapshots_missing_for(registry) -> "list[str]":
    """宣言した組のスナップショットが実在しない ref を挙げる（空なら合格）。"""
    return sorted(
        ref
        for ref, d in registry.items()
        if d.spread_point_snapshot is not None
        and not sss.snapshot_path(*d.spread_point_snapshot).is_file()
    )


def test_every_declared_snapshot_exists_in_the_ledger():
    """現行台帳で宣言された組はすべて実在する（宣言 1 件。下の 2 件の対照と対で読む）。"""
    # Arrange / Act / Assert
    assert snapshots_missing_for(REGISTRY) == []


def test_the_existence_check_accepts_a_real_snapshot(tmp_path):
    """正の対照: 実在する組は挙げない（偽陽性を作らない）。"""
    # Arrange
    registry = {"zz_ok": _tick_descriptor(tmp_path, spread_point_snapshot=(_SERVER, _SYMBOL))}

    # Act / Assert
    assert snapshots_missing_for(registry) == []


def test_the_existence_check_flags_a_missing_snapshot(tmp_path):
    """負の対照: 実在しない組を挙げる（検出力の実証）。"""
    # Arrange
    registry = {
        "zz_ng": _tick_descriptor(tmp_path, spread_point_snapshot=(_SERVER, "NO_SUCH_SYMBOL"))
    }

    # Act / Assert
    assert snapshots_missing_for(registry) == ["zz_ng"]


# --------------------------------------------------------------------------- #
# 4. 読み口 spread_point_of
# --------------------------------------------------------------------------- #
def test_spread_point_of_a_declared_ref_is_the_snapshot_point_size(tmp_path, monkeypatch, raw):
    """宣言ありの ref は、スナップショットの point_size（表経由で素の JSON から取った値）。"""
    # Arrange
    monkeypatch.setitem(
        REGISTRY, "zz_declared", _tick_descriptor(tmp_path, spread_point_snapshot=(_SERVER, _SYMBOL))
    )

    # Act
    got = _spread_point().spread_point_of("zz_declared")

    # Assert
    assert got == _expected_point(raw)


@pytest.mark.parametrize("ref", ["jp225_tick", "jp225_mt5", "zz_not_in_ledger"])
def test_spread_point_of_an_undeclared_ref_is_none_without_reading_a_snapshot(ref, monkeypatch):
    """宣言無し・台帳外は None。CX: 使わない point のためにスナップショットを読まない（発行 − 使用 = 0）。"""
    # Arrange
    reads = _spy_snapshot_reads(monkeypatch)

    # Act
    got = _spread_point().spread_point_of(ref)

    # Assert
    used = 0  # point を返していない
    assert got is None
    assert len(reads) - used == 0


def test_spread_point_of_a_ref_whose_snapshot_is_missing_is_fail_stop(tmp_path, monkeypatch):
    """スナップショットが無ければ SnapshotError（既定値へ落とさない）。"""
    # Arrange
    monkeypatch.setitem(
        REGISTRY, "zz_missing",
        _tick_descriptor(tmp_path, spread_point_snapshot=(_SERVER, "NO_SUCH_SYMBOL")),
    )

    # Act / Assert
    with pytest.raises(sss.SnapshotError, match="NO_SUCH_SYMBOL"):
        _spread_point().spread_point_of("zz_missing")


# --------------------------------------------------------------------------- #
# 5. spread_point.py に MT5 のフィールド名リテラルが無い
# --------------------------------------------------------------------------- #
def _mt5_field_names() -> "set[str]":
    names = {source.key for source in sss.SPEC_FIELD_SOURCES.values()}
    names.add(sss.SETTLEMENT_CURRENCY_SOURCE.key)
    return names


def leaked_mt5_field_names(tree: ast.AST) -> "list[str]":
    """docstring を除く文字列定数のうち MT5 のフィールド名であるものを挙げる（空なら合格）。"""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(getattr(node, "body", None), list)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    }
    return sorted(_mt5_field_names() & literals)


def test_spread_point_module_has_no_mt5_field_name_literal():
    """MT5 のフィールド名は表 SYMBOL_FIELD_SOURCES の中にだけ在る（第 2 の対応を作らない）。"""
    # Arrange
    tree = ast.parse(Path(_spread_point().__file__).read_text(encoding="utf-8"))

    # Act / Assert
    assert leaked_mt5_field_names(tree) == []


def test_the_literal_scan_flags_a_leaked_field_name():
    """負の対照: 本文の文字列に MT5 名を書けば挙がる（検出力の実証）。"""
    # Arrange
    leaked_key = sss.SYMBOL_FIELD_SOURCES[_POINT_NAME].key
    tree = ast.parse(f"KEY = {leaked_key!r}\n")

    # Act / Assert
    assert leaked_mt5_field_names(tree) == [leaked_key]


def test_the_literal_scan_ignores_docstrings():
    """正の対照: docstring の中の語は対応ではないので挙げない。"""
    # Arrange
    tree = ast.parse(f'"""{sss.SYMBOL_FIELD_SOURCES[_POINT_NAME].key}"""\n')

    # Act / Assert
    assert leaked_mt5_field_names(tree) == []


# --------------------------------------------------------------------------- #
# 6. 銘柄仕様の 1 項目読み（口座属性の表は引かない）
# --------------------------------------------------------------------------- #
def test_load_symbol_field_reads_one_entry_of_the_symbol_table(raw):
    """銘柄仕様の表の 1 項目を、表の型で返す。"""
    # Arrange / Act
    got = sss.load_symbol_field(_SERVER, _SYMBOL, _POINT_NAME)

    # Assert
    assert got == _expected_point(raw)
    assert isinstance(got, float)


@pytest.mark.parametrize("name", sorted(sss.ACCOUNT_FIELD_SOURCES))
def test_load_symbol_field_refuses_an_account_attribute_before_reading(name, monkeypatch):
    """口座属性（account 節）は銘柄仕様の表に無いので拒否し、スナップショットも読まない。"""
    # Arrange
    reads = _spy_snapshot_reads(monkeypatch)

    # Act / Assert
    with pytest.raises(KeyError):
        sss.load_symbol_field(_SERVER, _SYMBOL, name)
    assert reads == []


def test_load_symbol_field_is_fail_stop_when_the_key_is_absent(tmp_path, monkeypatch, raw):
    """表のキーがスナップショットに無ければ SnapshotError（既定値で埋めない）。"""
    # Arrange: point_size のキーだけ抜いた写しを tmp_path の SNAPSHOT_ROOT に置く。
    source = sss.SYMBOL_FIELD_SOURCES[_POINT_NAME]
    broken = copy.deepcopy(raw)
    del broken[source.section][source.key]
    (tmp_path / _SERVER).mkdir()
    (tmp_path / _SERVER / f"{_SYMBOL}.json").write_text(json.dumps(broken), encoding="utf-8")
    monkeypatch.setattr(sss, "SNAPSHOT_ROOT", tmp_path)

    # Act / Assert
    with pytest.raises(sss.SnapshotError, match=source.key):
        sss.load_symbol_field(_SERVER, _SYMBOL, _POINT_NAME)
