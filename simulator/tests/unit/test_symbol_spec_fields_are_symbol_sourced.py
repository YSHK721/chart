"""検出ゲート: ``SymbolSpec`` の全フィールドが供給元の ``symbol_info`` 由来であること。

由来: ISSUE-445 恒久策 **段階 3-D0**（``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.4）。
段階 0（``simulator/tests/integration/test_mt5_case_spec_agrees_with_report.py``）と同じ規律で
**値を 1 つも変えず、検出ゲートだけを先に置く**。

**現在は恒久の緑**（2026-08-26・段階 3-D2 で是正済み）。``SymbolSpec`` から ``leverage``
が外れ（7 フィールド）、口座属性は ``usecase/run_backtest.py:RunBacktestRequest`` が持つ。
是正を入れた瞬間、本ゲートは設計どおり **XPASS(strict)** で赤に転じ、``xfail`` マーカーの
撤去を機械的に促した（実測。この機構が働いた 3 例目である）。以後、本ファイルは
「銘柄の契約の型に別の契約の値が同居していない」ことを固定し続ける。

**固定する不変条件**: ``SymbolSpec`` の各フィールドは、供給元スナップショットの
``symbol`` セクション（＝ ``mt5.symbol_info()`` が返すもの）から引ける。

**なぜそれが不変条件なのか**: ``SymbolSpec`` は「銘柄の契約」を表す型である。銘柄の契約の
供給元は MT5 の ``symbol_info`` ただ 1 つであり、そこから引けない値が混じっているなら、
それは**別の契約（口座）の値が銘柄の型に同居している**ということである。変更起点が違う値が
1 つの型に同居するのは SRP 違反であり、ISSUE-445 の RC-1（人が書いた値が権威のように振る舞う）
が入り込む隙間そのものになる。

**かつてこれは 1 件だけ違反していた**（``leverage``・口座属性）。``mt5.symbol_info()`` が返す
96 フィールドに ``leverage`` は無く（ISSUE-445 実測 2026-08-25）、``tester.log:13`` も
``initial deposit 10000 JPY, leverage 1:10`` と**口座の行**に記録している。当時は CI を緑に
保つため ``xfail(strict=True)`` で「既知の不整合」として固定していた。段階 3-D2（2026-08-26）
で ``SymbolSpec`` から ``leverage`` を外したところ、宣言どおり **XPASS(strict)** で赤に転じ、
マーカー撤去を機械的に促した。この機構は段階 0 → 段階 2、段階 2 → 段階 3-A に続いて
**3 度目**も設計どおり働いた。

**空の分割で自明に緑にしない**: 「``SymbolSpec`` に account 由来が無い」は、対応表から
口座属性の供給ごと消しても成立してしまう。よって
:func:`test_the_supply_table_still_carries_account_sourced_fields_outside_the_symbol_spec`
が「口座由来の供給が**実在し**、かつ ``SymbolSpec`` の外にある」ことを対で固定する。

段階の呼称について（実測 2026-08-26・段階 3-D0 時点の記録）: 「3-D0 / 3-D1 / 3-D2」は依頼時の呼称であり、
**設計書には未記載**である（``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` の grep 実測 0 件。
記録済みの細分は 3-A / 3-B / 3-C / 3-E 系 / 3-F）。``ISSUE.md`` については、本ゲート新設時点の
実測は 0 件だったが、その新設自体を記録した時点から ISSUE-445 の作業記録として登場する
（2026-08-26 再実測 3 件）。**設計書 §3.4 が権威**であり、そこでの位置づけは
「分離自体は**段階 3 送り**」。
本ゲートの解消条件の実体は呼称に依存しない——``SymbolSpec`` から ``leverage`` が外れること
（あるいは ``symbol_info`` から引けるようになること。後者は供給元に当該キーが無いため
起こり得ない＝実測）である。

判定をテスト側のリテラルで持たない:
    期待値・違反キーの一覧をここに書かない。判定は :func:`fields_not_sourced_from_symbol_info`
    が :data:`SPEC_FIELD_SOURCES` の ``section`` と ``dataclasses.fields(SymbolSpec)`` から
    機械的に導く。将来 ``SymbolSpec`` にフィールドが増えれば（それが ``account`` 由来なら）
    自動で対象に入る。セクション名すら literal を置かず、対応表の単一ソースから引く
    （:data:`SYMBOL_INFO_SECTION` 参照）。

既存検定との住み分け（重複を作らない）:
    - ``simulator/tests/unit/test_symbol_spec_snapshot_field_parity.py`` — **銘柄仕様の表**の
      キー集合が ``SymbolSpec`` のフィールド名集合と一致すること（および口座属性の表の
      キーが ``RunBacktestRequest`` に行き先を持つこと）。本ゲートは同じ集合の
      **section** を見る。
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


def _account_sourced_table_entries() -> "FrozenSet[str]":
    """対応表のうち ``symbol_info`` 以外（＝口座）から引くエントリ名。

    判定は同じ純関数に投げる（判定を 2 度書かない）。対象を「``SymbolSpec`` の
    フィールド」から「対応表の全キー」に替えるだけで、口座由来の供給が引ける。
    """
    return fields_not_sourced_from_symbol_info(
        SPEC_FIELD_SOURCES, frozenset(SPEC_FIELD_SOURCES), SYMBOL_INFO_SECTION
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


# --- 固定する不変条件（段階 3-D2 で是正済み・恒久の緑）--------------------------------


def test_every_symbol_spec_field_is_sourced_from_symbol_info():
    """``SymbolSpec`` は銘柄の契約だけを持つ（口座の契約を持たない）。"""
    # Arrange / Act
    violations = _violations()
    # Assert
    assert violations == frozenset(), (
        "SymbolSpec のフィールドが symbol_info 以外から供給されている: "
        f"{sorted(violations)}。銘柄の契約の型に別の契約の値が同居している（SRP 違反）。"
    )


# --- 緑の検定（分離が「口座の契約を消した」形でないこと・その実在の証拠）---------------


@pytest.fixture(scope="module")
def snapshot() -> "dict[str, Any]":
    """供給元スナップショット（MT5 端末から機械取得したもの）。"""
    return load_snapshot(OANDA_JAPAN_MT5_LIVE, _SYMBOL)


def test_the_supply_table_still_carries_account_sourced_fields_outside_the_symbol_spec():
    """口座由来の供給は**実在し**、かつ ``SymbolSpec`` の外にある（段階 3-D2 の到達点）。

    段階 3-D0 ではここに「違反は 1 件だけ」という**当時の状態の記録**を置いていた。是正で
    違反が 0 件になり当該記録は赤になったため、実体に合わせて**問いを差し替えた**。
    単に消さないのは、上のゲート単体では「対応表から口座属性の供給ごと消す」形でも緑に
    なってしまうためである（分離ではなく削除でも通る＝空虚化）。本検定はその抜け道を塞ぐ:

        1. 対応表に ``symbol_info`` 以外から引くエントリが**残っている**（口座の契約が実在）。
        2. そのどれもが ``SymbolSpec`` のフィールドでは**ない**（同居していない）。

    2 件目の口座属性が増えても壊れない（件数を数えない）。逆に口座属性が ``SymbolSpec``
    へ戻ったら 2. が赤になる。
    """
    account_sourced = _account_sourced_table_entries()
    assert account_sourced, (
        "対応表に symbol_info 以外から引くエントリが 1 つも無い。"
        "口座の契約の供給が消えている（分離ではなく削除になっている）。"
    )
    intruders = account_sourced & _symbol_spec_field_names()
    assert not intruders, (
        f"口座由来のフィールドが SymbolSpec に同居している: {sorted(intruders)}"
    )


def test_the_account_sourced_fields_are_absent_from_the_symbol_info_section(snapshot):
    """口座由来のフィールドは実際に ``symbol_info`` の出力に存在しない（直接証拠）。

    「銘柄仕様ではない」を対応表の申告（``section`` の綴り）ではなく供給元の中身で裏付ける。
    ここが緑である限り、``section`` が ``symbol`` でないことは記述の都合ではなく実体である。
    走査対象は ``_violations()``（是正後は空＝空回りする）ではなく対応表の口座由来
    エントリであり、是正後も証拠を見続ける。
    """
    section = snapshot[SYMBOL_INFO_SECTION]
    names = _account_sourced_table_entries()
    assert names  # 空走で自明に緑にしない
    for name in names:
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
