"""検出ゲート: ``SymbolSpec`` の全フィールドが供給元の ``symbol_info`` 由来であること。

由来: ISSUE-445 恒久策 **段階 3-D0**（``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.4）。
段階 0（``simulator/tests/integration/test_mt5_case_spec_agrees_with_report.py``）と同じ規律で
**値を 1 つも変えず、検出ゲートだけを先に置く**。

**固定する不変条件**: ``SymbolSpec`` の各フィールドは、供給元スナップショットの
``symbol`` セクション（＝ ``mt5.symbol_info()`` が返すもの）から引ける。

**なぜそれが不変条件なのか**: ``SymbolSpec`` は「銘柄の契約」を表す型である。銘柄の契約の
供給元は MT5 の ``symbol_info`` ただ 1 つであり、そこから引けない値が混じっているなら、
それは**別の契約（口座）の値が銘柄の型に同居している**ということである。変更起点が違う値が
1 つの型に同居するのは SRP 違反であり、ISSUE-445 の RC-1（人が書いた値が権威のように振る舞う）
が入り込む隙間そのものになる。

**現状これは 1 件だけ違反している**（``leverage``・口座属性）。``mt5.symbol_info()`` が返す
96 フィールドに ``leverage`` は無く（ISSUE-445 実測 2026-08-25）、``tester.log:13`` も
``initial deposit 10000 JPY, leverage 1:10`` と**口座の行**に記録している。よって本ゲートは
**現時点で赤になるのが正しい**。CI を緑に保つため ``xfail(strict=True)`` で「既知の不整合」と
して固定し、是正が入ると **XPASS(strict)** で赤に転じて「マーカーを外せ」と機械的に知らせる。
この機構は段階 0 → 段階 2、段階 2 → 段階 3-A の 2 度とも設計どおり働いた（実例は段階 0 の
検出ゲートの docstring）。

**本段階では何も分離しない。** ``SymbolSpec`` からの ``leverage`` 分離は既存 IF
（``build_interactor`` の引数）に触れるため段階 3-D1 / 3-D2 の裁定に属する。

判定をテスト側のリテラルで持たない:
    期待値・違反キーの一覧をここに書かない。判定は :func:`fields_not_sourced_from_symbol_info`
    が :data:`SPEC_FIELD_SOURCES` の ``section`` と ``dataclasses.fields(SymbolSpec)`` から
    機械的に導く。将来 ``SymbolSpec`` にフィールドが増えれば（それが ``account`` 由来なら）
    自動で対象に入る。セクション名すら literal を置かず、対応表の単一ソースから引く
    （:data:`SYMBOL_INFO_SECTION` 参照）。

既存検定との住み分け（重複を作らない）:
    - ``simulator/tests/unit/test_symbol_spec_snapshot_field_parity.py`` — 対応表の**キー集合**が
      ``SymbolSpec`` のフィールド名集合と一致すること。本ゲートは同じ集合の **section** を見る。
    - ``marketdata/tests/test_symbol_spec_snapshot.py`` — 対応表が実スナップショット上で解決する
      こと・``leverage`` が ``account`` 由来であること。あちらは供給元ローダ側の記述であり、
      ``SymbolSpec``（``simulator`` 側の型）を知らない。本ゲートは型の側から見る。
"""
from __future__ import annotations

import dataclasses
from typing import Any, FrozenSet, Mapping

import pytest

from marketdata.symbol_spec_snapshot import (
    OANDA_JAPAN_MT5_LIVE,
    SETTLEMENT_CURRENCY_SOURCE,
    SPEC_FIELD_SOURCES,
    FieldSource,
    load_snapshot,
)
from simulator.usecase.models import SymbolSpec

_SYMBOL = "JP225"

#: ``mt5.symbol_info()`` の出力が入るスナップショットのセクション名。
#:
#: リテラルを置かずに対応表（単一ソース）から引く: 決済通貨は ``currency_profit`` から取るが、
#: これは ``symbol_info`` のフィールドである。よってその供給元セクションが symbol_info の
#: セクションである。この同定が崩れていないことは
#: :func:`test_the_symbol_info_section_holds_every_field_that_claims_it` が固定する。
SYMBOL_INFO_SECTION = SETTLEMENT_CURRENCY_SOURCE.section


# --- 判定（純関数・負の対照でも同じものを使う＝判定を 2 度書かない）-------------------


def fields_not_sourced_from_symbol_info(
    sources: "Mapping[str, FieldSource]",
    field_names: "FrozenSet[str]",
    symbol_info_section: str,
) -> "FrozenSet[str]":
    """``field_names`` のうち ``symbol_info`` から引けないものを返す（空集合なら合格）。

    対応表に載っていないフィールドも「引けない」に該当するため違反に含める（供給漏れが
    既定値で埋まる事態を、引けないことと区別せずに赤にする）。
    """
    return frozenset(
        name
        for name in field_names
        if name not in sources or sources[name].section != symbol_info_section
    )


def _symbol_spec_field_names() -> "FrozenSet[str]":
    return frozenset(f.name for f in dataclasses.fields(SymbolSpec))


def _violations() -> "FrozenSet[str]":
    return fields_not_sourced_from_symbol_info(
        SPEC_FIELD_SOURCES, _symbol_spec_field_names(), SYMBOL_INFO_SECTION
    )


# --- 負の対照（落ちないゲートは無価値であるため恒久テストとして固定する）--------------


def _source(section: str, key: str) -> FieldSource:
    return FieldSource(section, key, float)


class TestTheGateDetectsAndOnlyDetects:
    """判定関数が両方向に効くこと（検出する／余計に検出しない）を合成の対応表で固定する。

    実データではなく合成の対応表を食わせるのは、**本番の値を 1 つも変えずに**「是正が入れば
    緑になる」側を実証するためである。実データ側の赤（``xfail``）と対で読むこと。
    """

    def test_it_flags_a_field_sourced_from_another_section(self):
        # Arrange: 1 件だけ symbol_info 以外（口座）から引く対応表。
        table = {
            "point_size": _source(SYMBOL_INFO_SECTION, "point"),
            "leverage": _source("account", "leverage"),
        }
        # Act
        violations = fields_not_sourced_from_symbol_info(
            table, frozenset(table), SYMBOL_INFO_SECTION
        )
        # Assert
        assert violations == {"leverage"}

    def test_it_stays_silent_when_every_field_is_symbol_sourced(self):
        # Arrange: 同じ 2 フィールドを symbol_info 由来に是正した対応表（＝段階 3-D2 後の形）。
        table = {
            "point_size": _source(SYMBOL_INFO_SECTION, "point"),
            "leverage": _source(SYMBOL_INFO_SECTION, "leverage"),
        }
        # Act
        violations = fields_not_sourced_from_symbol_info(
            table, frozenset(table), SYMBOL_INFO_SECTION
        )
        # Assert
        assert violations == frozenset()

    def test_it_flags_a_field_absent_from_the_mapping_table(self):
        """供給漏れ（表に載っていない）も違反として検出する。"""
        # Arrange
        table = {"point_size": _source(SYMBOL_INFO_SECTION, "point")}
        # Act
        violations = fields_not_sourced_from_symbol_info(
            table, frozenset({"point_size", "swap_long"}), SYMBOL_INFO_SECTION
        )
        # Assert
        assert violations == {"swap_long"}

    def test_it_stays_silent_on_an_empty_field_set(self):
        """境界: 見るべきフィールドが無ければ違反も無い（偽陽性を作らない）。"""
        assert (
            fields_not_sourced_from_symbol_info(
                SPEC_FIELD_SOURCES, frozenset(), SYMBOL_INFO_SECTION
            )
            == frozenset()
        )


# --- 固定する不変条件（現時点では既知の不整合により赤）--------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ISSUE-445 の既知の不整合: leverage は口座属性であり symbol_info に存在しない"
        "（設計書 §3.4）。段階 3-D2 で SymbolSpec から leverage を分離したときに解消する。"
        "解消したら本マーカーを外すこと（XPASS(strict) が赤で知らせる）。"
    ),
)
def test_every_symbol_spec_field_is_sourced_from_symbol_info():
    """``SymbolSpec`` は銘柄の契約だけを持つ（口座の契約を持たない）。"""
    # Arrange / Act
    violations = _violations()
    # Assert
    assert violations == frozenset(), (
        "SymbolSpec のフィールドが symbol_info 以外から供給されている: "
        f"{sorted(violations)}。銘柄の契約の型に別の契約の値が同居している（SRP 違反）。"
    )


# --- 緑の検定（違反が 1 件に局在していること・その 1 件が実在の証拠を持つこと）---------


@pytest.fixture(scope="module")
def snapshot() -> "dict[str, Any]":
    """供給元スナップショット（MT5 端末から機械取得したもの）。"""
    return load_snapshot(OANDA_JAPAN_MT5_LIVE, _SYMBOL)


def test_the_violation_is_localised_to_a_single_field():
    """違反は 1 件だけである（段階 3-D0 時点の記録）。

    2 件目が生えたら赤になる。是正で 0 件になったときも赤になる（そのときは
    :func:`test_every_symbol_spec_field_is_sourced_from_symbol_info` の xfail が
    XPASS(strict) で赤になり、本検定と併せて「記録を更新せよ」と知らせる）。
    """
    fields = _symbol_spec_field_names()
    violations = _violations()
    assert len(violations) == 1
    symbol_sourced = fields - violations
    assert len(symbol_sourced) == len(fields) - 1
    assert all(
        SPEC_FIELD_SOURCES[name].section == SYMBOL_INFO_SECTION for name in symbol_sourced
    )


def test_the_violating_field_is_absent_from_the_symbol_info_section(snapshot):
    """違反フィールドは実際に ``symbol_info`` の出力に存在しない（直接証拠）。

    「銘柄仕様ではない」を対応表の申告（``section`` の綴り）ではなく供給元の中身で裏付ける。
    ここが緑である限り、``section`` が ``symbol`` でないことは記述の都合ではなく実体である。
    """
    section = snapshot[SYMBOL_INFO_SECTION]
    for name in _violations():
        assert SPEC_FIELD_SOURCES[name].key not in section


def test_the_section_claim_agrees_with_the_snapshot_contents(snapshot):
    """対応表の ``section`` 申告と供給元の中身が一致する（両含意）。

    ``section == symbol`` を主張するフィールドはそのキーが ``symbol`` セクションに実在し、
    主張しないフィールドはそのキーが ``symbol`` セクションに実在しない。片側だけを見ると
    「``account`` から引いているが ``symbol`` にも同名キーがある」を見逃す。
    """
    section = snapshot[SYMBOL_INFO_SECTION]
    for name in _symbol_spec_field_names():
        source = SPEC_FIELD_SOURCES[name]
        assert (source.section == SYMBOL_INFO_SECTION) == (source.key in section), name


def test_the_symbol_info_section_holds_every_field_that_claims_it(snapshot):
    """:data:`SYMBOL_INFO_SECTION` の同定が崩れていないこと（本ゲートの前提）。

    セクション名をリテラルで持たず ``SETTLEMENT_CURRENCY_SOURCE`` から導いているため、
    その導出が壊れると本ゲート全体が空虚になる。実スナップショットに当該セクションが実在し、
    それを主張する全フィールドのキーを保持していることを固定する。
    """
    assert SYMBOL_INFO_SECTION in snapshot
    claimed = {
        s.key for s in SPEC_FIELD_SOURCES.values() if s.section == SYMBOL_INFO_SECTION
    }
    assert claimed and claimed <= set(snapshot[SYMBOL_INFO_SECTION])
