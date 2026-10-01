"""N-17（気配幅を読む EA × 気配幅を供給しないデータ実体）の単体検定。

固定する不変条件:
    1. 気配幅を読む EA × 気配幅の列を持たない marketdata 形式で N-17 が発火する（Fail-Stop）。
    2. 気配幅を読まない EA（既定 TC 経路）× 同じ実体では発火しない。
    3. 気配幅を読む EA × MT5 TAB 形式（`<SPREAD>` あり）では発火しない（指紋経路無改変）。
    4. 判定源の列挙は**戦略の宣言**から導かれる（ISSUE-525）。

4 の測り方が変わった理由（ISSUE-525・2026-09-25）:
    是正前ここは「宣言集合 == ファクトリが 「`Mt5CsvOHLCRepository`」 を返す EA の集合」を
    表明していた。その等式は**データ形式を気配幅依存の代理として測るもの**であり、
    `WeeklyVolBand_EA` を取り逃していた——同 EA は判定の瞬間を ``current_open`` と名乗る
    （気配幅を約定価格へ内包する）のに、comma 系の読み方を使うため代理では非該当に
    分類され、気配幅を供給しないデータで完走した（実測: exit=0 / trades=1 /
    約定価格＝足の始値）。代理ではなく**宣言そのもの**を読む形へ替えた。

    宣言から導く列挙の中身と、宣言が約定クォートの実測と一致することは
    `simulator/tests/unit/test_spread_dependency_from_declaration.py` が持つ。本ファイルは
    「保証境界がその列挙を判定源にしている」ことだけを表明する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.main import spread_dependent_ea_names
from simulator.main.tester_settings.unsupported import NOT_VIOLATED, RULES
from simulator.tests.tester_settings_engine_fixtures import run_scope_inputs

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


def _detect(ea_name: str, data_path) -> object:
    """N-17 の判定式を 1 回発行する。

    ``ea_name`` は**語幹**である（合流点が運ぶのは語幹であり、`.ini` の `Expert` の生値
    ではない——写像層が 「`ea_stem`」 で直してから渡す）。
    """
    rule = RULES["N-17"]
    inputs = run_scope_inputs(ea_name=ea_name, data_path=str(data_path))
    return rule.detect(inputs, inputs)


def test_a_spread_dependent_ea_on_marketdata_form_fires(marketdata_csv):
    assert _detect("MA_Slope_EA", marketdata_csv) == "MA_Slope_EA"


def test_a_spread_independent_ea_on_marketdata_form_does_not_fire(marketdata_csv):
    assert _detect("TC24051901", marketdata_csv) is NOT_VIOLATED


def test_a_spread_dependent_ea_on_mt5_form_does_not_fire():
    # spread 列を持つ MT5 TAB 形式なら従来どおり実行できる（fixture 経路無改変）
    assert _MT5_FIXTURE.is_file(), "MT5 fixture が見つかりません（前提の崩れ）"
    assert _detect("MA_Slope_EA", _MT5_FIXTURE) is NOT_VIOLATED


def test_the_boundary_reads_the_declaration_derived_enumeration(marketdata_csv):
    """判定源が宣言から導いた列挙であること（発火する EA 名の集合そのもので測る）。

    列挙に載る名前はすべて発火し、載らない実行可能名は 1 つも発火しない。判定源を
    差し替えたり縮めたりすれば、どちらかが崩れる。
    """
    from simulator.main import known_ea_names

    derived = set(spread_dependent_ea_names())
    assert derived, "列挙が空（前提の崩れ）"
    fired = {
        name
        for name in known_ea_names()
        if _detect(name, marketdata_csv) is not NOT_VIOLATED
    }
    assert fired == derived
