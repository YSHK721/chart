"""2 本目の系列（気配幅つき）をカタログへ載せたことの検定（ISSUE-511 段階 8-D-3）。

何を固定するか:
    カタログは同じ銘柄・同じ供給元の**別の実体**を 2 本提供する。両者を分ける識別子は
    dataset ref（``RunProfile.dataset``）ただ 1 つであり、**投入 body は 1 バイトも増えない**
    ——front の投入キー許可リスト（``PROFILE_KEYS``）に ``dataset`` は無い。

なぜ順序を固定するのか（**実測された危険**・2026-09-25・本作業ツリー）:
    共有フィクスチャと既存検定は profile を「先頭」または「銘柄一致の先頭」で引いている
    （``simulator/tests/tester_settings_engine_fixtures.py`` の 2 箇所・
    ``simulator/tests/unit/test_tester_settings_engine_fixtures.py``・
    ``simulator/sim_ui/tests/integration/test_run_job_settings_extensions.py``、および
    ``simulator/sim_ui/tests/unit/test_symbol_spec_catalog.py`` の
    ``[p for p in ... if p.symbol == "JP225"][0]`` 群）。
    2 本目を先頭へ入れると、これらは**赤にならずに別系列で走る**。数え方: 2 本目を先頭に
    置いた変異を当て、datasets()/run-options に触る 10 ファイル（83 件）を走らせた。正しい
    並びでは 103.67 秒で終わるのに対し、入替版は **29 分を超えても終わらず**、失敗ではなく
    実行対象の入替（12 年系列 / 6.4 年系列）としてのみ現れた。出力は形式上正しいままなので
    状態検証では原理的に検出できない。よって並びをここで表明する。

綴りを書き写さない:
    1 本目は カタログ自身が名乗る ref（``_JP225_REF``）から、2 本目は**台帳の宣言**から導く
    （気配幅を宣言している ref＝``spread_point_snapshot`` を持つ記述子）。綴りを書き写すと、
    カタログや台帳が別の ref を名乗るようになっても検定が古い綴りを測り続ける。
    投入キーの一覧も front の現物から読む（ここに 11 個の綴りを並べない）。

計算量（CX）:
    継ぎ目は 3 つ——ヘッダ読取（ohlc_marketdata_csv の _header_line）・範囲読取
    （カタログの _csv_date_range）・供給元スナップショット読取（load_snapshot）。表明は
    **発行 − 相異なる実体の数 = 0** である。「相異なる実体」はデータ側が 2（別々の CSV）、
    スナップショット側は 1（2 本は同じ ``(サーバ, 銘柄)`` を指す）であり、**同じ
    スナップショットを 2 回読む形はここで落ちる**。規模 2 点（5 行 / 5,000 行）で発行が
    増えないことも併せて表明する。**回数そのものは焼き込まない**。

共有するテストヘルパは import して使う（同じものを手書き複製しない）:
    Test Spy … `marketdata/tests/spread_series_fixture.py`
    ヘッダ定数・本文・書き出し … `simulator/tests/ohlc_header_fixtures.py`
"""
from __future__ import annotations

import re
from pathlib import Path

from marketdata.dataset_registry import REGISTRY, whitelist
from marketdata.symbol_spec_snapshot import settlement_currency, spec_fields
from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.sim_ui.adapter import symbol_spec_catalog

from simulator.tests.ohlc_header_fixtures import MD6, MD9, body_rows, write_header

#: 1 本目の ref（カタログ自身の名乗りから引く。綴りを書き写さない）。
_LEGACY_REF = symbol_spec_catalog._JP225_REF

#: 注入された基準値であることを示す番兵（エンジンの語彙ではない）。
_INJECTED = "__injected_basis__"

#: 供給元から引いていることを示す番兵の決済通貨（実在の通貨コードではない）。
_SENTINEL_CURRENCY = "__sentinel_currency__"

#: 気配幅に依存しない EA（EA 名の注入元は本ファイルの関心外なので最小の束縛）。
_INDEPENDENT_EA = "TC24051901"

#: front の投入キー許可リストの現物（`PROFILE_KEYS` の宣言）。カタログの所在から導く
#: （web 配下は**読むだけ**であり、本段では 1 バイトも変えない＝UI は 8-D-5 の担当）。
_SUBMISSION_KEYS_SOURCE = (
    Path(symbol_spec_catalog.__file__).resolve().parents[1]
    / "web" / "js" / "adapter" / "front" / "sim_submission_builder.js"
)


def _submission_keys() -> "tuple[str, ...]":
    """front が投入 body へ写す profile 由来キーを現物から読む（綴りをここに並べない）。"""
    source = _SUBMISSION_KEYS_SOURCE.read_text(encoding="utf-8")
    block = re.search(r"PROFILE_KEYS = Object\.freeze\(\[(.*?)\]\)", source, re.S)
    assert block, f"投入キー許可リストの宣言が読めません: {_SUBMISSION_KEYS_SOURCE}"
    return tuple(re.findall(r'"([A-Za-z_]+)"', block.group(1)))


def _ledger_spread_refs() -> "tuple[str, ...]":
    """台帳が**気配幅を宣言している** ref（``spread_point_snapshot`` を持つ記述子）。"""
    return tuple(
        ref for ref, d in REGISTRY.items() if d.spread_point_snapshot is not None
    )


def _catalog(basis: str = _INJECTED):
    """基準値を注入したカタログ（EA 名の注入元は本ファイルの関心外なので最小の束縛）。"""
    return symbol_spec_catalog.SymbolSpecCatalog(
        known_ea_names=lambda: (_INDEPENDENT_EA,), entry_price_basis=basis
    )


# --- 1. 何本・どの順で提供するか -----------------------------------------------------


def test_the_catalog_offers_the_legacy_series_first_and_the_spread_series_second():
    """提供は 2 本であり、**並びは（従来の系列, 気配幅つきの系列）に固定**されている。

    先頭が従来の ref であることは、先頭で profile を引いている共有フィクスチャと既存検定の
    前提そのものである（module docstring「なぜ順序を固定するのか」）。
    """
    # Arrange
    declared_spread = _ledger_spread_refs()

    # Act
    offered = [p.dataset for p in _catalog().datasets()]

    # Assert
    assert len(declared_spread) == 1, "台帳の気配幅系列が一意でない（対照が空虚になる）"
    assert declared_spread[0] != _LEGACY_REF   # 空振り防止（2 つは別の ref）
    assert offered == [_LEGACY_REF, declared_spread[0]]


def test_each_series_points_at_its_own_ledger_declaration():
    """2 本はそれぞれ**自分の ref の台帳宣言**を指す（実体は別々である）。"""
    # Act
    profiles = _catalog().datasets()

    # Assert
    for profile in profiles:
        assert profile.data_path == str(whitelist()[profile.dataset])
    assert len({p.data_path for p in profiles}) == len(profiles)   # 空振り防止（別の実体）


# --- 2. 投入本文は増えない -----------------------------------------------------------


def test_the_dataset_label_is_not_one_of_the_submission_keys():
    """2 本を分ける識別子（``dataset``）は投入 body の profile 由来キーに含まれない。"""
    # Act
    keys = _submission_keys()

    # Assert
    assert keys, "投入キーが 1 つも読めていない（空振り）"
    assert "dataset" not in keys


def test_both_series_supply_the_same_submission_keys_and_differ_only_in_the_entity():
    """どちらの系列を選んでも投入 body の**キー集合は同一**であり、値は実体だけが違う。

    キーが増えないこと（＝既存 byte 等価契約が動かないこと）と、系列の違いが実体 1 キー
    にしか現れないことを同時に表明する。
    """
    # Arrange
    keys = _submission_keys()

    # Act
    bodies = [{k: p.to_dict()[k] for k in keys} for p in _catalog().datasets()]

    # Assert
    for profile in _catalog().datasets():
        assert set(keys) <= set(profile.to_dict())
    assert bodies[0]["data_path"] != bodies[1]["data_path"]      # 空振り防止（別の実体）
    assert {k: v for k, v in bodies[0].items() if k != "data_path"} == {
        k: v for k, v in bodies[1].items() if k != "data_path"
    }


# --- 3. 2 本目の銘柄仕様も供給元スナップショット由来 ---------------------------------


def test_the_second_series_symbol_spec_follows_the_supply_snapshot(monkeypatch):
    """2 本目の銘柄仕様・決済通貨も供給元から引く（リテラルを持たない）。

    供給元の読込を差し替えると 2 本目の 8 項目と決済通貨が追随する。カタログが値の
    リテラルを持っていれば追随しない。
    """
    # Arrange
    real = symbol_spec_catalog.load_snapshot
    baseline = real(symbol_spec_catalog._JP225_SERVER, symbol_spec_catalog._JP225_SYMBOL)

    def doctored(server, symbol):
        snapshot = dict(real(server, symbol))
        snapshot["symbol"] = dict(snapshot["symbol"])
        snapshot["symbol"]["digits"] = int(baseline["symbol"]["digits"]) + 3
        snapshot["symbol"]["currency_profit"] = _SENTINEL_CURRENCY
        return snapshot

    expected = spec_fields(
        doctored(symbol_spec_catalog._JP225_SERVER, symbol_spec_catalog._JP225_SYMBOL)
    )
    monkeypatch.setattr(symbol_spec_catalog, "load_snapshot", doctored)

    # Act
    second = _catalog().datasets()[1]

    # Assert
    assert expected["digits"] != int(baseline["symbol"]["digits"])   # 空振り防止（差し替えた）
    assert len(expected) == 8
    for name, value in expected.items():
        assert getattr(second, name) == value, name
    assert second.settlement_currency == _SENTINEL_CURRENCY


# --- 4. 計算量（CX）: 発行 − 相異なる実体の数 = 0 -------------------------------------


def _measure(monkeypatch, tmp_path, rows: int) -> dict:
    """``rows`` 行の実体 2 つを両系列に当て、`datasets()` 1 回の発行と相異なる実体を数える。"""
    legacy = write_header(tmp_path, f"legacy_{rows}.csv", MD6, body_rows(MD6, rows))
    spread = write_header(tmp_path, f"spread_{rows}.csv", MD9, body_rows(MD9, rows))
    monkeypatch.setattr(symbol_spec_catalog, "_JP225_DATA_CSV", Path(legacy))
    monkeypatch.setattr(symbol_spec_catalog, "_JP225_SPREAD_DATA_CSV", Path(spread))

    header_reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")
    range_reads = spy(monkeypatch, symbol_spec_catalog, "_csv_date_range")
    snapshot_reads = spy(monkeypatch, symbol_spec_catalog, "load_snapshot")

    profiles = _catalog().datasets()
    return {
        "header": len(header_reads),
        "range": len(range_reads),
        "snapshot": len(snapshot_reads),
        # 相異なる実体: データ側は別々の CSV、スナップショット側は読んだ組（サーバ, 銘柄）。
        "data_entities": len({p.data_path for p in profiles}),
        "snapshot_entities": len(set(snapshot_reads)),
        "overrides": [p.config_overrides for p in profiles],
    }


def test_the_reads_do_not_grow_with_the_number_of_series_or_the_data(monkeypatch, tmp_path):
    """3 継ぎ目すべてが 発行 − 相異なる実体の数 = 0 であり、データ行数でも増えない。

    2 本が同じ ``(サーバ, 銘柄)`` を指すため、スナップショットの相異なる実体は 1 である。
    同じスナップショットを系列ごとに読み直す形は出力を 1 ビットも変えないため状態検証では
    落ちない——ここが唯一その無駄を止める。**回数そのものは焼き込まない**。
    """
    # Act
    small = _measure(monkeypatch, tmp_path, 5)
    monkeypatch.undo()
    large = _measure(monkeypatch, tmp_path, 5_000)
    monkeypatch.undo()

    # Assert
    for measured in (small, large):
        # 空振り防止: 実際に読んでおり、両方の実体の答えを使っている
        assert measured["snapshot"] >= 1
        assert measured["data_entities"] > 1
        assert measured["overrides"][0] is None
        assert measured["overrides"][1] == {"entry_price_basis": _INJECTED}
        assert measured["header"] - measured["data_entities"] == 0
        assert measured["range"] - measured["data_entities"] == 0
        assert measured["snapshot"] - measured["snapshot_entities"] == 0
    assert large["header"] == small["header"]       # 行数 1,000 倍でも発行は増えない
    assert large["range"] == small["range"]
    assert large["snapshot"] == small["snapshot"]
