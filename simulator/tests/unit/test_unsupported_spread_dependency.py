"""N-17（spread 依存 EA × spread 無しデータ形式）の単体検定（2026-09-06・全期間データ結線）。

固定する不変条件:
    1. spread 依存 EA × marketdata 形式データで N-17 が発火する（Fail-Stop）。
    2. spread 非依存 EA（TC 等）× marketdata 形式では発火しない。
    3. spread 依存 EA × MT5 TAB 形式（spread あり）では発火しない（fixture 経路無改変）。
    4. 宣言 `SPREAD_DEPENDENT_EA_NAMES` は EA ファクトリ表の実体（Mt5CsvOHLCRepository を
       返す EA）と一致する（宣言の写しが腐らない機械の結び）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.main.tester_settings.unsupported import (
    NOT_VIOLATED,
    RULES,
    SPREAD_DEPENDENT_EA_NAMES,
)
from simulator.tests.tester_settings_engine_fixtures import engine_binding, runnable_settings

_ROOT = Path(__file__).resolve().parents[3]
_MT5_FIXTURE = (
    _ROOT / "simulator" / "tests" / "fixtures" / "mt5" / "ma_slope_jp225_202501"
    / "input" / "JP225_M1_202501.csv"
)


@pytest.fixture
def marketdata_csv(tmp_path):
    path = tmp_path / "md.csv"
    path.write_text("date,open,high,low,close,volume\n2024-01-08 00:00:00,1,1,1,1,1\n")
    return path


def _detect(ea_file: str, data_path) -> object:
    rule = RULES["N-17"]
    settings = runnable_settings(Expert=ea_file)
    return rule.detect(
        settings.effective(),
        engine_binding(
            data_path=str(data_path),
            known_ea_names=tuple(SPREAD_DEPENDENT_EA_NAMES) + ("TC24051901",),
        ),
    )


def test_a_spread_dependent_ea_on_marketdata_form_fires(marketdata_csv):
    assert _detect("MA_Slope_EA.ex5", marketdata_csv) == "MA_Slope_EA"


def test_a_spread_independent_ea_on_marketdata_form_does_not_fire(marketdata_csv):
    assert _detect("TC24051901.ex5", marketdata_csv) is NOT_VIOLATED


def test_a_spread_dependent_ea_on_mt5_form_does_not_fire():
    # spread 列を持つ MT5 TAB 形式なら従来どおり実行できる（fixture 経路無改変）
    assert _MT5_FIXTURE.is_file(), "MT5 fixture が見つかりません（前提の崩れ）"
    assert _detect("MA_Slope_EA.ex5", _MT5_FIXTURE) is NOT_VIOLATED


def _measured_spread_dependent_eas() -> "set[str]":
    """ファクトリ表を実際に呼び「Mt5CsvOHLCRepository を返す EA」の集合を測る。

    判定は各ファクトリを MT5 fixture で実際に呼び、返る repository の型で行う
    （comma 系ファクトリは MT5 TAB を読めず例外＝非該当）。分岐は本ヘルパに閉じ、
    テスト本体は単一の等式表明だけを持つ。
    """
    import simulator.main as sim_main

    ctx = sim_main._EaBuildContext(
        data_path=str(_MT5_FIXTURE), ma_period=20, ma_method="sma", adx_period=14,
        weekly_forecast=None, weekly_p_tp=0.5, weekly_capital=1_000_000.0, weekly_f_risk=0.1,
    )
    measured = set()
    for ea_name, factory in sim_main._EA_FACTORIES.items():
        try:
            _, _, repo = factory(ctx)
        except Exception:
            continue   # MT5 TAB を読めない＝comma 系（spread 非依存）
        if isinstance(repo, Mt5CsvOHLCRepository):
            measured.add(ea_name)
    return measured


def test_the_declared_set_matches_the_factory_table():
    """宣言集合 == 「ファクトリが Mt5CsvOHLCRepository を返す EA」の集合（機械の結び）。

    unsupported.py は循環回避のため `simulator.main` を import できず宣言を写しで持つ。
    写しが腐ればここが落ちる（測り方は `_measured_spread_dependent_eas` に閉じる）。
    """
    assert _measured_spread_dependent_eas() == set(SPREAD_DEPENDENT_EA_NAMES)
