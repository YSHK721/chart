"""台帳からしか受け取らない項目（ISSUE-546 レビュー 🟡-2）。

ストップアウト水準は台帳の口座 margin_so_so ただ 1 つが出所である。銘柄仕様の 8 項目は
CLI の明示指定が台帳に優先する（what-if 実行）が、水準まで同じ扱いにすると「人が書いた値が
台帳に優先する入口」が CLI に残る。どの項目がそうかは marketdata に 1 か所だけ宣言し
（「`LEDGER_ONLY_FIELDS`」）、受付（sim 画面）と CLI の両方がそれを参照する。
"""
from __future__ import annotations

import pytest

from marketdata.symbol_spec_snapshot import (
    LEDGER_ONLY_FIELDS,
    OANDA_JAPAN_MT5_LIVE,
    SPEC_FIELD_SOURCES,
    load_spec_fields,
)
from simulator.sim_ui.main import composition_root_jobs
from simulator.tests.unit.test_cli_symbol_spec_args import _CLIS, _parse
from simulator.tools.symbol_spec_args import (
    SPEC_KEYS,
    SymbolSpecArgsError,
    resolve_symbol_spec,
    spec_option,
)

_SYMBOL = "JP225"
_UNREGISTERED = "NO_SUCH_SYMBOL"


def test_the_declaration_names_the_stop_out_level_and_only_ledger_fields():
    assert "stop_out_level" in LEDGER_ONLY_FIELDS
    assert LEDGER_ONLY_FIELDS <= set(SPEC_FIELD_SOURCES)


def test_the_submission_side_refers_to_the_same_declaration():
    assert composition_root_jobs.LEDGER_SUPPLIED_KEYS is LEDGER_ONLY_FIELDS


def test_the_cli_overridable_keys_exclude_the_ledger_only_fields():
    assert set(SPEC_KEYS) == set(SPEC_FIELD_SOURCES) - LEDGER_ONLY_FIELDS


@pytest.mark.parametrize("name", sorted(_CLIS))
def test_the_clis_offer_no_option_for_a_ledger_only_field(name):
    parser = _CLIS[name][0]()
    options = {opt for action in parser._actions for opt in action.option_strings}
    assert {spec_option(key) for key in LEDGER_ONLY_FIELDS} & options == set()


@pytest.mark.parametrize("name", sorted(_CLIS))
@pytest.mark.parametrize("key", sorted(LEDGER_ONLY_FIELDS))
def test_specifying_a_ledger_only_field_on_the_cli_stops(name, key):
    with pytest.raises(SystemExit):
        _parse(name, [spec_option(key), "50"])


@pytest.mark.parametrize("name", sorted(_CLIS))
def test_the_resolved_spec_carries_the_ledger_value(name):
    resolved = resolve_symbol_spec(_parse(name))
    ledger = load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)
    assert {key: resolved[key] for key in LEDGER_ONLY_FIELDS} == {
        key: ledger[key] for key in LEDGER_ONLY_FIELDS
    }


def test_an_unregistered_symbol_stops_even_with_every_option_explicit():
    """台帳が引けなければ、他の項目を全部明示しても水準を作らずに止める。"""
    spec = load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)
    explicit: "list[str]" = ["--symbol", _UNREGISTERED]
    for key in SPEC_KEYS:
        explicit += [spec_option(key), str(spec[key])]
    with pytest.raises(SymbolSpecArgsError) as caught:
        resolve_symbol_spec(_parse("run_is_oos_cli", explicit))
    assert all(key in str(caught.value) for key in LEDGER_ONLY_FIELDS)
