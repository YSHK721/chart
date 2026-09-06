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


def test_data_path_points_to_the_full_marketdata_jp225_csv():
    """data_path は 2012 年からの全期間 JP225 実データ（marketdata 形式）を指す。

    依頼者承認 2026-09-06。読み手は `MarketdataCsvOHLCRepository`（形式はヘッダが権威）。
    spread 依存 EA はこのデータでは N-17 が実行前に弾く。
    """
    from pathlib import Path

    from simulator.adapter.repository.ohlc_marketdata_csv import detect_ohlc_form

    jp = [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]
    assert jp.dataset == "jp225_m1"
    p = Path(jp.data_path)
    assert p.name == "jp225_m1.csv"
    assert p.is_file(), f"data_path の CSV が実在しない: {jp.data_path}"
    assert detect_ohlc_form(p) == "marketdata"


def test_config_overrides_follow_the_data_form():
    """決定論設定の override はデータ実体の形式から導く（宣言は _config_overrides_for）。

    MT5 TAB 形式のみ current_open（MT5 ローダ EA が close 系列を持たない・実測）。
    marketdata 形式（現行データセット）は override なし＝既定 close。
    """
    from pathlib import Path

    from simulator.sim_ui.adapter.symbol_spec_catalog import _config_overrides_for

    jp = [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]
    assert jp.config_overrides is None
    assert "config_overrides" not in jp.to_dict()
    mt5_fixture = (
        Path(jp.data_path).parents[2] / "simulator" / "tests" / "fixtures" / "mt5"
        / "ma_slope_jp225_202501" / "input" / "JP225_M1_202501.csv"
    )
    assert mt5_fixture.is_file(), "MT5 fixture が見つかりません（前提の崩れ）"
    assert _config_overrides_for(mt5_fixture) == {"entry_price_basis": "current_open"}


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

    # ⚠ ISSUE-445 段階 C: 下の `RunProfile(...)` の銘柄仕様 8 項目は供給元スナップショットから
    # 引く（段階 B までは `contract_size=10.0` ほか供給元と食い違うリテラルを書いていた）。
    # ただし**値は本検定の結果に 1 ビットも効かない**——見ているのは
    # `settlement_currency` 省略時の `TypeError` だけであり、8 項目を真値へ寄せても
    # 変わらず緑だった（実測 2026-08-26）。よって数値ピンを足す余地は無い。
    # 本検定の緑を「銘柄仕様が正しい」根拠にしてはならない。
    # 本ファイルで値の正しさを見ているのは
    # `test_jp225_profile_symbol_spec_comes_from_the_supply_snapshot` だけであり、
    # そちらは供給元と 8 項目を突合している（期待値をこのファイルに書いていない）。
    from marketdata.symbol_spec_snapshot import (
        OANDA_JAPAN_MT5_LIVE,
        load_spec_fields,
    )

    with pytest.raises(TypeError):
        RunProfile(
            dataset="x", data_path="/x.csv", symbol="JP225", period="M1",
            **load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225"),
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


def test_data_range_is_measured_from_the_csv_itself():
    """データ範囲（先頭/末尾の日付トークン）は CSV 実体からの実測であること。

    プリセット選択時に日付ボックスへ表示する解決期間のデータ源（表示専用）。先頭は
    全期間データの先頭（2012-06-14・不変の実測 golden）。末尾はライブ供給で日々進む
    ため値を焼き込まず、トークン形と順序（first <= last）だけを固定する。
    """
    import re

    jp = [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]
    assert jp.data_first_date == "2012.06.14"
    assert re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", jp.data_last_date)
    assert jp.data_first_date <= jp.data_last_date   # トークンは辞書順＝時系列順


def test_data_range_is_not_in_the_submission_payload_keys():
    """データ範囲は投入 body の profile 由来 11 キーに**含まれない**こと（表示専用）。

    to_dict（API 応答）には載るが、front の投入キー許可リスト（PROFILE_KEYS）が写す
    11 キーの側に混ざると byte 等価契約が壊れる。ここでは to_dict に載ることだけ固定し、
    投入側の不変は既存の byte 等価 golden（submission_body_parity）が担う。
    """
    jp = [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]
    payload = jp.to_dict()
    assert payload["data_first_date"] == jp.data_first_date
    assert payload["data_last_date"] == jp.data_last_date
