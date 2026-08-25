"""SymbolSpecCatalog（run config の銘柄仕様・データセット単一ソース）の単体検定（Phase 6 拡張）.

固定する不変条件（憶測禁止・供給元スナップショット由来で確定）:
    1. datasets() は JP225 の RunProfile を返す。銘柄仕様 8 項目（contract_size/digits/
       point_size/leverage/stops_level/volume_min/volume_max/volume_step）は**供給元
       スナップショット**（`marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`）と
       一致する。従来は case.yaml のリテラル（contract_size=10 / stops_level=0）を
       ここに書き写していたが、前者は出所の無い逆算値・後者はオラクル不在の値であり、
       ISSUE-445 段階 2 で権威を供給元へ移した。値の突合の詳細（供給元と独立な report.json
       導出との一致）は `sim_ui/tests/integration/test_run_options_mt5_gate.py` が持つ。
    2. data_path は dataset_registry.whitelist() の単一ソース由来（ハードコードしない）。
    3. ea_names() は注入元（`simulator.main.known_ea_names`）から導出（ハードコード禁止・
       束縛は Composition Root が持つ・ISSUE-405）。
    4. RunProfile は 11 の backtest プロファイルキー＋dataset ラベルを持つ。
"""
from __future__ import annotations

from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile


def test_is_run_options_port():
    assert isinstance(build_run_options_port(), RunOptionsPort)


def test_jp225_profile_symbol_spec_comes_from_the_supply_snapshot():
    # Arrange: 銘柄仕様の唯一の権威（MT5 端末から機械取得したスナップショット）。
    from marketdata.symbol_spec_snapshot import (
        OANDA_JAPAN_MT5_LIVE,
        load_spec_fields,
    )

    expected = load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225")
    # Act
    profiles = build_run_options_port().datasets()
    jp = [p for p in profiles if p.symbol == "JP225"][0]
    # Assert: 8 項目すべてが供給元と等値（カタログがリテラルを持たないことの実証）。
    assert len(expected) == 8
    for name, value in expected.items():
        assert getattr(jp, name) == value, f"{name}: カタログ {getattr(jp, name)!r} != 供給元 {value!r}"
    assert jp.symbol == "JP225" and jp.period == "M1"


def test_data_path_points_to_readable_mt5_jp225_csv():
    """data_path は MT5 形式の実 JP225 CSV（run ローダが読める形式）を指す。

    出典（憶測禁止）: dataset_registry の jp225_m1.csv（date/volume・time/spread 無し）は
    comma ローダでも MT5 ローダでも読めない（実測）。MT5 ローダ EA が読める MT5 突合 fixture
    と同系譜の実 OANDA-Japan MT5 JP225 M1（JP225_M1_202501.csv・TAB <DATE>）へ向ける。
    本番データ配置は未確定＝別 ISSUE。
    """
    from pathlib import Path

    jp = [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]
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
    jp = [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]
    assert jp.config_overrides == {"entry_price_basis": "current_open"}
    # to_dict にも載る（run-options 応答 → フォームへ届く）
    assert jp.to_dict()["config_overrides"] == {"entry_price_basis": "current_open"}


def test_ea_names_come_from_the_engine_accessor():
    """一覧の権威はエンジン（`known_ea_names`）。カタログは中継するだけ。"""
    from simulator.main import known_ea_names

    names = build_run_options_port().ea_names()
    assert names == list(known_ea_names())
    # 既定 TC 経路も投入可能な選択肢として含む
    assert "TC24051901" in names
    # 決定的（ソート済み・重複なし）
    assert names == sorted(set(names))


def test_ea_names_are_not_hardcoded_in_the_catalog():
    """注入元を差し替えれば一覧が変わる＝表を書き写していないことの実証。"""
    catalog = SymbolSpecCatalog(known_ea_names=lambda: ("A_EA", "B_EA"))
    assert catalog.ea_names() == ["A_EA", "B_EA"]


def test_ea_names_source_has_no_default_binding():
    """R-4 と同型: 注入は必須引数（adapter → main の外向き依存を作らない）。"""
    import inspect

    parameter = inspect.signature(SymbolSpecCatalog.__init__).parameters["known_ea_names"]
    assert parameter.default is inspect.Parameter.empty


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
    jp = [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]
    assert jp.to_dict()["settlement_currency"] == jp.settlement_currency


def test_run_profile_exposes_eleven_backtest_keys():
    jp = [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]
    d = jp.to_dict()
    for key in (
        "data_path", "symbol", "period", "contract_size", "digits", "point_size",
        "leverage", "volume_min", "volume_max", "volume_step", "stops_level",
    ):
        assert key in d, key
