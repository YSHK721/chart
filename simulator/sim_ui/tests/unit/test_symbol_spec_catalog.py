"""SymbolSpecCatalog（run config の銘柄仕様・データセット単一ソース）の単体検定（Phase 6 拡張）.

固定する不変条件（憶測禁止・MT5 突合 fixture 由来で確定）:
    1. datasets() は JP225 の RunProfile を返す。結果に効く定数（contract_size/digits/
       point_size/leverage/stops_level）は MT5 fixture case.yaml と一致（reconcile golden 再現）。
    2. data_path は dataset_registry.whitelist() の単一ソース由来（ハードコードしない）。
    3. ea_names() は simulator.main._EA_FACTORIES の keys＋既定 TC 経路から導出（ハードコード禁止）。
    4. RunProfile は 11 の backtest プロファイルキー＋dataset ラベルを持つ。
"""
from __future__ import annotations

from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile


def test_is_run_options_port():
    assert isinstance(SymbolSpecCatalog(), RunOptionsPort)


def test_jp225_profile_result_affecting_constants_match_fixture():
    # Arrange / Act
    profiles = SymbolSpecCatalog().datasets()
    # Assert: JP225 の結果に効く定数（case.yaml 権威値）
    jp = [p for p in profiles if p.symbol == "JP225"][0]
    assert jp.contract_size == 10.0
    assert jp.digits == 1
    assert jp.point_size == 0.1
    assert jp.leverage == 10.0
    assert jp.stops_level == 0
    assert jp.symbol == "JP225" and jp.period == "M1"


def test_data_path_points_to_readable_mt5_jp225_csv():
    """data_path は MT5 形式の実 JP225 CSV（run ローダが読める形式）を指す。

    出典（憶測禁止）: dataset_registry の jp225_m1.csv（date/volume・time/spread 無し）は
    comma ローダでも MT5 ローダでも読めない（実測）。MT5 ローダ EA が読める MT5 突合 fixture
    と同系譜の実 OANDA-Japan MT5 JP225 M1（JP225_M1_202501.csv・TAB <DATE>）へ向ける。
    本番データ配置は未確定＝別 ISSUE。
    """
    from pathlib import Path

    jp = [p for p in SymbolSpecCatalog().datasets() if p.symbol == "JP225"][0]
    assert jp.dataset == "jp225_m1"
    p = Path(jp.data_path)
    assert p.name == "JP225_M1_202501.csv"
    assert p.is_file(), f"data_path の CSV が実在しない: {jp.data_path}"
    # 先頭行が MT5 TAB 形式（<DATE> <TIME> … <SPREAD>）であることを実測で固定する。
    head = p.read_text(encoding="utf-8").splitlines()[0]
    assert "<DATE>" in head and "<SPREAD>" in head and "\t" in head


def test_mt5_profile_supplies_current_open_basis():
    """MT5 ローダ EA は建値系列に close を持たず open を持つため、profile が
    entry_price_basis=current_open を config_overrides で権威供給する（既定 close は失敗）。"""
    jp = [p for p in SymbolSpecCatalog().datasets() if p.symbol == "JP225"][0]
    assert jp.config_overrides == {"entry_price_basis": "current_open"}
    # to_dict にも載る（run-options 応答 → フォームへ届く）
    assert jp.to_dict()["config_overrides"] == {"entry_price_basis": "current_open"}


def test_ea_names_derived_from_ea_factories_plus_default():
    from simulator.main import _EA_FACTORIES

    names = SymbolSpecCatalog().ea_names()
    # _EA_FACTORIES の全 key が含まれる（ハードコードでなく単一ソース由来）
    for key in _EA_FACTORIES:
        assert key in names
    # 既定 TC 経路も投入可能な選択肢として含む
    assert "TC24051901" in names
    # 決定的（ソート済み・重複なし）
    assert names == sorted(set(names))


def test_settlement_currency_is_required_without_default():
    """`RunProfile.settlement_currency` は既定値を持たない（D-10 と同型の Fail-Stop）。

    「たぶん JPY」を DTO の既定値に置くと、通貨を持たないデータセットが沈黙で通貨一致
    （N-11 非該当）扱いになる。省略時は構築時点で `TypeError` にする。
    """
    import dataclasses

    import pytest

    field = {f.name: f for f in dataclasses.fields(RunProfile)}["settlement_currency"]
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING

    with pytest.raises(TypeError):
        RunProfile(
            dataset="x", data_path="/x.csv", symbol="JP225", period="M1",
            contract_size=10.0, digits=1, point_size=0.1, leverage=10.0,
            volume_min=0.01, volume_max=100.0, volume_step=0.01, stops_level=0,
        )


def test_settlement_currency_reaches_the_run_options_payload():
    """権威値が to_dict（＝run-options 応答）に載る。値の出典突合は MT5 ゲート側が持つ。"""
    jp = [p for p in SymbolSpecCatalog().datasets() if p.symbol == "JP225"][0]
    assert jp.to_dict()["settlement_currency"] == jp.settlement_currency


def test_run_profile_exposes_eleven_backtest_keys():
    jp = [p for p in SymbolSpecCatalog().datasets() if p.symbol == "JP225"][0]
    d = jp.to_dict()
    for key in (
        "data_path", "symbol", "period", "contract_size", "digits", "point_size",
        "leverage", "volume_min", "volume_max", "volume_step", "stops_level",
    ):
        assert key in d, key
