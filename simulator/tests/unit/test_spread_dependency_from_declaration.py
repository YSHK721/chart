"""気配幅への依存を**戦略の宣言**から導く（ISSUE-525・手書き列挙の撤去）。

何を解くか:
    保証境界 N-17 は「気配幅を供給しないデータで、気配幅を読む EA を走らせない」という
    宣言である。その「気配幅を読む EA」は是正前、`unsupported.py` の**手書きの 3 つの
    名前**（``{"MA_Slope_EA", "MA_Slope_Pending_EA", "StopEntryProbe_EA"}``）だった。
    手書きの列挙は取り残しを生む——実測（2026-09-25・本作業ツリー・HEAD ed17a928）:

        `WeeklyVolBand_EA` は判定の瞬間を ``current_open`` と名乗る（気配幅を読む側）のに
        列挙に無く、気配幅を供給しない合成 marketdata 6 列で **exit=0 / trades=1 /
        約定価格 40000.0（＝足の始値）** で完走した。

    ISSUE-533 以降、判定の瞬間の権威は戦略の宣言（`entry_price_databasis` ではなく
    ``entry_price_basis``）にある。したがって「気配幅を読むか」はその宣言から導ける。

固定する不変条件:
    D1  建値基準が気配幅を読むかの宣言（`basis_consumes_bar_spread`）は、約定クォートの
        **実測**と一致する（気配幅だけを変えて出力が動くか）。宣言の 3 値すべてで測る。
    D2  「気配幅を読む EA」の列挙は、各 EA 束縛が名乗る戦略の宣言から導かれる
        （名前を 1 つも書き写さない）。
    D3  宣言は EA ごとに分かれている（同じ答えを返す退化ではない）。
    D4  取り残しだった `WeeklyVolBand_EA` が入り、終値で判定する EA は入らない。
    D5  EA 束縛が名乗る戦略の型は、その束縛が実際に組む戦略の型と一致する。
    D6  手書きの列挙は残っていない。
"""
from __future__ import annotations

import pytest

from simulator.main.ea_bindings import (
    DEFAULT_EA_NAME,
    _EA_BINDINGS,
    known_ea_names,
    spread_dependent_ea_names,
)
from simulator.main.ea_bindings.binding import EaBuildContext
from simulator.usecase._execution import derive_quotes
from simulator.usecase.entry_price_basis import (
    DECLARABLE_BASES,
    NO_BAR_BOUNDARY_DECISION,
    EntryPriceBasisDeclarationError,
    basis_consumes_bar_spread,
    declared_entry_price_basis,
)
from simulator.usecase.pending_lifecycle import PendingLifecycleEngine

#: 実測に使う足（気配幅以外は固定する）。
_PRICE = 40000.0
_POINT = 0.1
#: 0 と非 0 の 2 点で測る（1 点では「動くか」を問えない）。
_SPREADS = (0, 7)


class _Bar:
    """気配幅だけを差し替えられる足（約定クォートが読む 2 値だけを持つ）。"""

    def __init__(self, spread: int) -> None:
        self.open = _PRICE
        self.close = _PRICE + 20.0
        self.spread = spread


def _measured_consumes_bar_spread(basis: "str | None") -> bool:
    """その建値基準の約定クォートが足の気配幅を読むかを**実測**する。

    足境界で判定する宣言（`DECLARABLE_BASES`）は `derive_quotes` が約定クォートを導く。
    足境界で判定しない宣言（`NO_BAR_BOUNDARY_DECISION`）はそこで Fail-Stop するので、
    その戦略が実際に約定する口——ティック／ペンディングのクォート規約
    （`PendingLifecycleEngine.tick_quote`）——で測る。どちらも「気配幅だけを変えて
    出力が動くか」という同じ問いである。
    """
    if basis is NO_BAR_BOUNDARY_DECISION:
        quotes = [
            PendingLifecycleEngine.tick_quote(_PRICE, spread=spread, point_size=_POINT)
            for spread in _SPREADS
        ]
        return quotes[0] != quotes[1]
    try:
        quotes = [
            derive_quotes(_Bar(spread), entry_price_basis=basis, point_size=_POINT)
            for spread in _SPREADS
        ]
    except EntryPriceBasisDeclarationError:  # pragma: no cover - 語彙の外は D1 の対象外
        raise AssertionError(f"宣言の語彙のはずの値で約定クォートを導けない: {basis!r}")
    return quotes[0] != quotes[1]


def _declared_basis_of(binding) -> "str | None":
    return declared_entry_price_basis(binding.strategy_type)


class TestTheDeclarationAgreesWithTheMeasuredQuotes:
    """D1: 宣言と約定クォートの実測が一致する。"""

    @pytest.mark.parametrize(
        "basis", (*DECLARABLE_BASES, NO_BAR_BOUNDARY_DECISION), ids=repr
    )
    def test_the_declaration_matches_the_measurement(self, basis):
        assert basis_consumes_bar_spread(basis) is _measured_consumes_bar_spread(basis)

    def test_the_measurement_separates_the_bases(self):
        # 空振り防止: すべて True（またはすべて False）なら D1 は何も測っていない。
        measured = {
            basis: _measured_consumes_bar_spread(basis)
            for basis in (*DECLARABLE_BASES, NO_BAR_BOUNDARY_DECISION)
        }
        assert set(measured.values()) == {True, False}


class TestTheEnumerationIsDerivedFromTheDeclarations:
    """D2 / D3 / D4。"""

    def test_the_enumeration_equals_what_the_declarations_say(self):
        derived = {
            binding.name
            for binding in _EA_BINDINGS.values()
            if basis_consumes_bar_spread(_declared_basis_of(binding))
        }
        # 既定 TC 経路は登録表の外側にある実行可能名であり、列挙はそれも覆う。
        assert set(spread_dependent_ea_names()) - {DEFAULT_EA_NAME} == derived

    def test_the_enumeration_only_names_executable_eas(self):
        assert set(spread_dependent_ea_names()) <= set(known_ea_names())

    def test_the_declarations_are_not_all_the_same(self):
        declared = {_declared_basis_of(b) for b in _EA_BINDINGS.values()}
        assert len(declared) >= 2, f"宣言が分かれていない: {declared}"

    def test_the_ea_that_was_missing_from_the_hand_written_list_is_now_included(self):
        assert "WeeklyVolBand_EA" in spread_dependent_ea_names()

    def test_a_close_deciding_ea_is_not_included(self):
        assert DEFAULT_EA_NAME not in spread_dependent_ea_names()
        assert "SmaTouchLong_EA" not in spread_dependent_ea_names()

    def test_the_previously_listed_eas_are_still_included(self):
        # 是正で**緩めない**ことの表明（手書き列挙の 3 本は導出後も残る）。
        for name in ("MA_Slope_EA", "MA_Slope_Pending_EA", "StopEntryProbe_EA"):
            assert name in spread_dependent_ea_names()

    def test_the_enumeration_is_deterministic(self):
        names = spread_dependent_ea_names()
        assert list(names) == sorted(names)
        assert len(set(names)) == len(names)
        assert spread_dependent_ea_names() == names


class TestTheDeclaredStrategyTypeMatchesWhatIsBuilt:
    """D5: 名乗った戦略の型が、実際に組まれる戦略の型と一致する。"""

    def test_every_binding_declares_the_type_it_builds(self, tmp_path):
        from simulator.tests.route_parity_fixtures import (
            weekly_params,
            write_marketdata_csv,
        )

        data_path = write_marketdata_csv(tmp_path / "md.csv", with_spread=True)
        params = {
            "ma_period": 2, "ma_method": "sma", "adx_period": 14, "adx_min": 22.0,
            "lot_size": 1.0, "stop_loss_points": 0.0, "take_profit_points": 0.0,
            **weekly_params(),
        }
        built = 0
        for binding in _EA_BINDINGS.values():
            strategy, _registry, _repository = binding.build(
                EaBuildContext(data_path=str(data_path), params=params)
            )
            assert type(strategy) is binding.strategy_type, binding.name
            built += 1
        assert built == len(_EA_BINDINGS), "全束縛を組めていない（実測の空振り）"


class TestNoHandWrittenEnumerationRemains:
    """D6: 手書きの列挙が残っていない。"""

    def test_the_boundary_module_no_longer_declares_ea_names(self):
        from simulator.main.tester_settings import unsupported

        assert not hasattr(unsupported, "SPREAD_DEPENDENT_EA_NAMES")
