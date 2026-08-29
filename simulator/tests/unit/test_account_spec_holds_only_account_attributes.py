"""退行防止ゲート: ``AccountSpec`` は**口座の契約だけ**を持ち、既定値を持たない。

由来: ISSUE-445 恒久策 **段階 3-D3**（案 D・``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.4）。
段階 3-D2 で ``SymbolSpec`` から外した口座属性は ``RunBacktestRequest`` に**3 つフラットに**
置かれていた。本段階でそれを ``AccountSpec`` 1 つへ束ね、銘柄の契約（``SymbolSpec``）と
口座の契約（``AccountSpec``）を型で 2 軸に分けた。

**既存ゲートとの住み分け（重複を作らない）**:
    - ``test_symbol_spec_fields_are_symbol_sourced.py`` — 「口座属性が ``SymbolSpec`` へ
      戻らない」向き。本ファイルはその**鏡像**（「銘柄仕様が ``AccountSpec`` へ混ざらない」）
      だけを見る。判定関数はあちらの :func:`fields_not_sourced_from_symbol_info` を
      **import して再利用**する（同じ判定を 2 度書かない）。
    - ``test_symbol_spec_snapshot_field_parity.py`` — 対応表のキー集合と消費側の型の
      フィールド名の一致（``ACCOUNT_FIELD_SOURCES`` の行き先が ``AccountSpec`` に実在する
      こと）。本ファイルは対応表の**行き先**ではなく型の**中身**を見る。
    - ``test_leverage_reaches_required_margin.py`` — ``leverage`` が末端まで届くこと。
      本ファイルは値の到達を見ない（型の形だけを見る）。

**固定する不変条件は 4 つ**:
    1. ``AccountSpec`` のどのフィールドも ``symbol_info``（銘柄仕様の供給元）から引けない。
       すなわち銘柄の契約が口座の型へ混ざっていない。
    2. 口座属性の家は 1 つである——``RunBacktestRequest`` は口座属性を**フラットに持たず**、
       ``AccountSpec`` 型の受け口を 1 つ持つ。フラットな面が復活したら赤になる。
    3. ``AccountSpec`` は**既定値を 1 つも持たない**。既定値は「人が書いた値が権威になる」形
       （ISSUE-445 RC-1）と同型であり、口座の契約（初期証拠金・必要証拠金の除数・
       ストップアウト水準）を誰も指定しないまま run が通る経路を作る。
    4. ``AccountSpec`` は不変（frozen）である。可変な口座**状態**は ``domain/account.py``
       の ``Account``（``apply_deal`` / ``update_floating_pnl`` を持つ集約・当該 docstring が
       「値オブジェクト方針の例外として可変」と明記）が担う。契約と状態が 2 つとも可変だと、
       run の途中で契約側を書き換える経路が型の上で開く。

判定をテスト側のリテラルで持たない:
    フィールド名・セクション名・件数をここに書かない。判定は供給元の対応表
    （``marketdata/symbol_spec_snapshot.py``）と ``dataclasses.fields`` から機械的に導く。
"""
from __future__ import annotations

import dataclasses
from typing import Any, FrozenSet, Type, get_type_hints

import pytest

from marketdata.symbol_spec_snapshot import SPEC_FIELD_SOURCES
from simulator.tests.unit.test_symbol_spec_fields_are_symbol_sourced import (
    SYMBOL_INFO_SECTION,
    fields_not_sourced_from_symbol_info,
)
from simulator.usecase.models import AccountSpec
from simulator.usecase.run_backtest import RunBacktestRequest


# --- 判定（純関数・負の対照でも同じものを使う＝判定を 2 度書かない）-------------------


def _field_names(dataclass_type: Any) -> "FrozenSet[str]":
    return frozenset(f.name for f in dataclasses.fields(dataclass_type))


def symbol_sourced_fields_among(field_names: "FrozenSet[str]") -> "FrozenSet[str]":
    """``field_names`` のうち銘柄仕様の供給元（``symbol_info``）から引けるものを返す。

    「引けない」の判定は既存ゲートの純関数をそのまま使い、その**補集合**を取る。
    ``symbol_info`` から引けるフィールドが口座の型に居るなら、それは銘柄の契約の混入である。
    """
    not_symbol_sourced = fields_not_sourced_from_symbol_info(
        SPEC_FIELD_SOURCES, field_names, SYMBOL_INFO_SECTION
    )
    return field_names - not_symbol_sourced


def fields_carrying_a_default(dataclass_type: Any) -> "FrozenSet[str]":
    """既定値（``default`` / ``default_factory``）を持つフィールド名を返す。"""
    return frozenset(
        f.name
        for f in dataclasses.fields(dataclass_type)
        if f.default is not dataclasses.MISSING
        or f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
    )


def fields_typed_as(dataclass_type: Any, wanted: type) -> "FrozenSet[str]":
    """型注釈が ``wanted`` そのものであるフィールド名を返す（文字列比較をしない）。"""
    hints = get_type_hints(dataclass_type)
    return frozenset(
        f.name for f in dataclasses.fields(dataclass_type) if hints.get(f.name) is wanted
    )


# --- 負の対照（落ちないゲートは無価値であるため恒久テストとして固定する）--------------


def _synthetic(name: str, fields: "list[tuple]", *, frozen: bool) -> Type[Any]:
    return dataclasses.make_dataclass(name, fields, frozen=frozen)


class TestTheGateDetectsAndOnlyDetects:
    """判定関数が両方向に効くこと（検出する／余計に検出しない）を合成の型で固定する。

    本番の型を 1 つも変えずに「違反が入れば赤になる」側を実証する。
    """

    def test_it_flags_a_symbol_sourced_field_inside_an_account_type(self):
        # Arrange: 供給元の銘柄仕様の表から 1 件を借りて口座の型へ混ぜる（名前は書かない）。
        intruder = sorted(
            name
            for name, source in SPEC_FIELD_SOURCES.items()
            if source.section == SYMBOL_INFO_SECTION
        )[0]
        contaminated = _field_names(AccountSpec) | {intruder}
        # Act
        found = symbol_sourced_fields_among(contaminated)
        # Assert
        assert found == {intruder}

    def test_it_stays_silent_on_the_account_fields_alone(self):
        assert symbol_sourced_fields_among(_field_names(AccountSpec)) == frozenset()

    def test_it_stays_silent_on_an_empty_field_set(self):
        """境界: 見るべきフィールドが無ければ混入も無い（偽陽性を作らない）。"""
        assert symbol_sourced_fields_among(frozenset()) == frozenset()

    def test_the_default_scan_flags_a_synthesised_default(self):
        # Arrange: 既定値ありの合成型。
        with_default = _synthetic(
            "_WithDefault", [("a", float), ("b", float, dataclasses.field(default=0.0))],
            frozen=True,
        )
        # Act / Assert
        assert fields_carrying_a_default(with_default) == {"b"}

    def test_the_default_scan_stays_silent_without_defaults(self):
        without_default = _synthetic("_NoDefault", [("a", float)], frozen=True)
        assert fields_carrying_a_default(without_default) == frozenset()

    def test_the_type_scan_finds_only_the_wanted_annotation(self):
        # Arrange: 1 件だけ AccountSpec 型を持つ合成型。
        holder = _synthetic(
            "_Holder", [("other", float), ("account", AccountSpec)], frozen=True
        )
        # Act / Assert
        assert fields_typed_as(holder, AccountSpec) == {"account"}
        assert fields_typed_as(_synthetic("_None", [("x", float)], frozen=True), AccountSpec) == (
            frozenset()
        )


# --- 固定する不変条件 -----------------------------------------------------------------


def test_the_account_spec_carries_no_symbol_contract():
    """不変条件 1: 銘柄仕様が口座の型へ混ざっていない。"""
    names = _field_names(AccountSpec)
    assert names, "AccountSpec が空（空の型で自明に緑にしない）"
    intruders = symbol_sourced_fields_among(names)
    assert not intruders, (
        f"銘柄仕様の供給元から引けるフィールドが AccountSpec に同居している: {sorted(intruders)}。"
        "口座の契約の型に銘柄の契約が混ざっている（SRP 違反）。"
    )


def test_the_run_request_holds_the_account_contract_in_exactly_one_place():
    """不変条件 2: 口座属性の家は 1 つ（フラットな面が復活していない）。"""
    request_fields = _field_names(RunBacktestRequest)
    account_fields = _field_names(AccountSpec)
    assert account_fields  # 空の型で自明に緑にしない
    flattened = request_fields & account_fields
    assert not flattened, (
        f"RunBacktestRequest が口座属性をフラットに持っている: {sorted(flattened)}。"
        "口座の契約の所在が 2 つになっている。"
    )
    holders = fields_typed_as(RunBacktestRequest, AccountSpec)
    assert len(holders) == 1, (
        f"RunBacktestRequest の AccountSpec 受け口が 1 つでない: {sorted(holders)}"
    )


def test_the_account_spec_invents_no_value():
    """不変条件 3: 既定値を 1 つも持たない（RC-1 の再生産を型で止める）。"""
    invented = fields_carrying_a_default(AccountSpec)
    assert not invented, (
        f"AccountSpec が既定値を持っている: {sorted(invented)}。"
        "口座の契約を誰も指定しないまま run が通る経路になる（ISSUE-445 RC-1 と同型）。"
    )


def test_the_account_spec_is_immutable():
    """不変条件 4: 契約は不変（可変なのは ``domain.Account`` の側だけ）。"""
    assert dataclasses.is_dataclass(AccountSpec)
    spec = AccountSpec(initial_deposit=1.0, leverage=1.0, stop_out_level=0.0)
    target = sorted(_field_names(AccountSpec))[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, target, 2.0)
