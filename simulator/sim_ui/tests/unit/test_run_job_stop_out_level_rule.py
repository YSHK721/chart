"""run_job がストップアウト水準を引く規則と、その読みの量（ISSUE-546）。

規則は決済通貨（`test_run_job_profile_identity.py`）と同じ「一致 → 合意 → 停止」で、
並びに依存しない。値は**実在しない番兵の水準**を profile に載せて測る——番兵が返ること
自体が「その profile の値が来た」証拠になる（実在の値だと偶然の一致と区別できない）。

計算量（絶対命令・宣言した注入点だけ）:
    束（「`EngineBinding`」）1 つを組むのに、カタログ（台帳）の読み（`datasets()`）は決済通貨と
    水準で共有する。読みを値ごとに発行すると出力は同じまま読みだけが増える（状態検証では
    落ちない）。注入点は Composition Root の工場 `composition_root_jobs.build_run_options_port`
    （既存の検定 `test_run_job_profile_identity.py` が使う継ぎ目と同じ）だけである。
    表明は「読み − 組んだ束の数 = 0」を、束の数（1 / 3）と profile 数（2 / 6）の 2 点ずつで。
"""
from __future__ import annotations

from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.sim_ui.main import composition_root_jobs, run_job
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile
from simulator.usecase.models import SymbolSpec
from simulator.usecase.tester_settings import TickModel

_SYMBOL = "JP225"
#: 台帳からしか受け取らない項目のうち本検定が測るもの（宣言 LEDGER_ONLY_FIELDS の要素）。
_KEY = "stop_out_level"
#: 実在しない番兵の水準（どちらの profile の値が来たかを一意に言える）。
_LEVEL_A = 1234.5
_LEVEL_B = 6789.25
_FOREIGN_ENTITY = "/nowhere/synthetic_submission.csv"


def _profile(dataset: str, data_path: str, level: float) -> RunProfile:
    spec = load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL)
    return RunProfile(
        dataset=dataset, data_path=data_path, symbol=_SYMBOL, period="M1",
        **{**spec, "stop_out_level": level},
        settlement_currency="JPY",
    )


def _pair(*, disagree: bool) -> "list[RunProfile]":
    return [
        _profile("first", "/entities/first.csv", _LEVEL_A),
        _profile("second", "/entities/second.csv", _LEVEL_B if disagree else _LEVEL_A),
    ]


class _CountingPort(RunOptionsPort):
    """`datasets()` の発行を数える RunOptionsPort（Test Spy）。"""

    def __init__(self, profiles):
        self._profiles = list(profiles)
        self.reads = 0

    def datasets(self):
        self.reads += 1
        return list(self._profiles)

    def ea_names(self):
        return ["TC24051901"]


def _backtest_of(profile: RunProfile) -> dict:
    body = {f.name: getattr(profile, f.name) for f in fields(SymbolSpec)}
    body.update(
        symbol=profile.symbol, period=profile.period, data_path=profile.data_path,
        leverage=profile.leverage,
    )
    return body


_EFFECTIVE = SimpleNamespace(tick_model=TickModel.MATH_CALCULATIONS)


# --- 1. 規則（一致 → 合意 → 停止） -----------------------------------------------


#: 2 本の並べ方（検定本体で分岐しないため、並びを引数の側で与える）。
_ORDERS = {"declared": (0, 1), "reversed": (1, 0)}


@pytest.mark.parametrize("order", sorted(_ORDERS))
def test_the_submitted_entity_identifies_the_level_whatever_the_order(order):
    profiles = _pair(disagree=True)
    ordered = [profiles[i] for i in _ORDERS[order]]
    got = run_job._ledger_values_for_submission(
        ordered, symbol=_SYMBOL, data_path=profiles[1].data_path
    )[_KEY]
    assert got == _LEVEL_B


def test_an_unmatched_entity_uses_the_agreed_level():
    got = run_job._ledger_values_for_submission(
        _pair(disagree=False), symbol=_SYMBOL, data_path=_FOREIGN_ENTITY
    )[_KEY]
    assert got == _LEVEL_A


def test_an_unmatched_entity_stops_when_the_levels_disagree():
    with pytest.raises(run_job.LedgerValueDisagreement) as caught:
        run_job._ledger_values_for_submission(
            _pair(disagree=True), symbol=_SYMBOL, data_path=_FOREIGN_ENTITY
        )[_KEY]
    assert str(_LEVEL_A) in str(caught.value) and str(_LEVEL_B) in str(caught.value)


def test_an_unregistered_symbol_stops_instead_of_using_a_default():
    with pytest.raises(ValueError) as caught:
        run_job._ledger_values_for_submission(
            _pair(disagree=False), symbol="NOT_A_SYMBOL", data_path=_FOREIGN_ENTITY
        )[_KEY]
    assert "NOT_A_SYMBOL" in str(caught.value)


# --- 2. 結線: 両経路の水準がこの規則を通って来る -------------------------------------


def test_the_engine_binding_takes_its_level_through_the_entity_rule(monkeypatch):
    profiles = list(reversed(_pair(disagree=True)))   # 先頭は _LEVEL_B の系列
    monkeypatch.setattr(
        composition_root_jobs, "build_run_options_port", lambda: _CountingPort(profiles)
    )
    wanted = profiles[1]
    binding = run_job._build_engine_binding({"backtest": _backtest_of(wanted)}, _EFFECTIVE)
    assert wanted.stop_out_level == _LEVEL_A          # 空振り防止（先頭ではない側）
    assert binding.stop_out_level == _LEVEL_A


def test_the_run_without_settings_takes_its_level_through_the_entity_rule(monkeypatch):
    profiles = list(reversed(_pair(disagree=True)))
    monkeypatch.setattr(
        composition_root_jobs, "build_run_options_port", lambda: _CountingPort(profiles)
    )
    wanted = profiles[1]
    assert run_job._ledger_values(_backtest_of(wanted))[_KEY] == _LEVEL_A


#: 両経路の組み立て（検定本体で分岐しないため、経路を引数の側で与える）。
_BUILDS = {
    "binding": lambda backtest: run_job._build_engine_binding({"backtest": backtest}, _EFFECTIVE),
    "legacy": lambda backtest: run_job._ledger_values(backtest),
}


@pytest.mark.parametrize("build", sorted(_BUILDS))
def test_a_level_in_the_submission_is_refused(monkeypatch, build):
    profiles = _pair(disagree=False)
    monkeypatch.setattr(
        composition_root_jobs, "build_run_options_port", lambda: _CountingPort(profiles)
    )
    backtest = {**_backtest_of(profiles[0]), "stop_out_level": _LEVEL_B}
    with pytest.raises(ValueError) as caught:
        _BUILDS[build](backtest)
    assert "stop_out_level" in str(caught.value)


def test_the_submission_api_does_not_accept_a_level():
    """受付の許容集合に水準は無い（投入から受け取らない）。必須集合にも無い。"""
    assert "stop_out_level" not in composition_root_jobs.allowed_backtest_keys()
    assert "stop_out_level" not in composition_root_jobs.required_backtest_keys()


# --- 3. 計算量: 台帳の読みは束 1 つにつき決済通貨と水準で共有する ------------------


def _profiles(count: int) -> "list[RunProfile]":
    base = _profile("p0", "/entities/p0.csv", _LEVEL_A)
    return [replace(base, dataset=f"p{i}", data_path=f"/entities/p{i}.csv") for i in range(count)]


@pytest.mark.parametrize("bindings", [1, 3])
@pytest.mark.parametrize("profile_count", [2, 6])
def test_the_catalog_is_read_once_per_binding(monkeypatch, bindings, profile_count):
    profiles = _profiles(profile_count)
    port = _CountingPort(profiles)
    monkeypatch.setattr(composition_root_jobs, "build_run_options_port", lambda: port)
    for _ in range(bindings):
        run_job._build_engine_binding({"backtest": _backtest_of(profiles[0])}, _EFFECTIVE)
    assert port.reads - bindings == 0
