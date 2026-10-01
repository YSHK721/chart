"""2 本目の系列（気配幅つき）をカタログへ載せたことの検定（ISSUE-511 段階 8-D-3）。

何を固定するか:
    カタログは同じ銘柄・同じ供給元の**別の実体**を複数提供し、その中に取引している供給の
    気配幅つき系列が在る。系列を分ける識別子は dataset ref（``RunProfile.dataset``）ただ 1 つで
    あり、**投入 body は 1 バイトも増えない**——front の投入キー許可リスト（``PROFILE_KEYS``）に
    ``dataset`` は無い。

    提供が台帳の宣言から導かれること（ISSUE-533 段階 3）と、提供が 3 本以上になっても並びの
    先頭が従来の系列であることは
    `simulator/tests/unit/test_symbol_spec_catalog_ledger_offering.py` が持つ。ここが持つのは
    「取引している供給の気配幅系列が、従来の系列より後ろに在る」ことである。

なぜ順序を固定するのか（**実測された危険**・2026-09-25・本作業ツリー）:
    共有フィクスチャと既存検定は profile を「先頭」または「銘柄一致の先頭」で引いている
    （``simulator/tests/tester_settings_engine_fixtures.py`` の 2 箇所・
    ``simulator/tests/unit/test_tester_settings_engine_fixtures.py``・
    ``simulator/sim_ui/tests/integration/test_run_job_settings_extensions.py``、および
    ``simulator/sim_ui/tests/unit/test_symbol_spec_catalog.py`` の
    ``[p for p in ... if p.symbol == "JP225"][0]`` 群）。
    気配幅つきを先頭へ入れると、これらは**赤にならずに別系列で走る**。数え方: 2 本目を先頭に
    置いた変異を当て、datasets()/run-options に触る 10 ファイル（83 件）を走らせた。正しい
    並びでは 103.67 秒で終わるのに対し、入替版は **29 分を超えても終わらず**、失敗ではなく
    実行対象の入替（12 年系列 / 6.4 年系列）としてのみ現れた。出力は形式上正しいままなので
    状態検証では原理的に検出できない。よって並びをここで表明する。

綴りを書き写さない:
    従来の系列は**台帳が提供すると宣言した先頭**から、気配幅つきは**台帳の宣言**から導く
    （気配幅を宣言している ref＝``spread_point_snapshot`` を持つ記述子）。綴りを書き写すと、
    台帳が別の ref を名乗るようになっても検定が古い綴りを測り続ける。
    投入キーの一覧も front の現物から読む（ここに 11 個の綴りを並べない）。

計算量（CX）:
    継ぎ目は 3 つ——ヘッダ読取（ohlc_marketdata_csv の _header_line）・範囲読取
    （カタログの _csv_date_range）・供給元スナップショット読取（load_snapshot）。表明は
    **発行 − 相異なる実体の数 = 0** である。「相異なる実体」はデータ側が提供された系列の数
    （別々の CSV）、スナップショット側は 1（提供はすべて同じ ``(サーバ, 銘柄)`` を指す）であり、
    **同じスナップショットを系列ごとに読み直す形はここで落ちる**。規模 2 点（5 行 / 5,000 行）で
    発行が増えないことも併せて表明する。**回数そのものは焼き込まない**。

共有するテストヘルパは import して使う（同じものを手書き複製しない）:
    Test Spy … `marketdata/tests/spread_series_fixture.py`
    ヘッダ定数・本文・書き出し … `simulator/tests/ohlc_header_fixtures.py`
    台帳の実体の差し替え … `simulator/tests/ledger_entity_fixtures.py`
"""
from __future__ import annotations

import re
from pathlib import Path

from marketdata.dataset_registry import (
    DEFAULT_DATASET_REF,
    REGISTRY,
    sim_offered_refs,
    whitelist,
)
from marketdata.symbol_spec_snapshot import settlement_currency, spec_fields
from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.sim_ui.adapter import symbol_spec_catalog

from simulator.tests.ledger_entity_fixtures import point_entity_at
from simulator.tests.ohlc_header_fixtures import MD6, MD9, body_rows, write_header

#: 従来の系列の ref（台帳が提供すると宣言した先頭から引く。綴りを書き写さない）。
_LEGACY_REF = sim_offered_refs()[0]

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
    """台帳が**気配幅を宣言している** ref のうち、**実際に取引している供給**のもの。

    台帳の気配幅系列は 1 件ではない（ISSUE-533 段階 3 の前提工事で Dukascopy 側にも対が
    できた）。sim が走るのは実際に取引している口座の系列なので、既定 datasetRef
    （``DEFAULT_DATASET_REF``＝取引している供給）のベンダで絞る。これで対照は一意に戻り、
    かつ綴りは 1 つも書き写さない——カタログが別の供給の気配幅系列を名乗るようになれば、
    ここが食い違いとして落ちる。
    """
    traded = REGISTRY[DEFAULT_DATASET_REF].vendor
    return tuple(
        ref for ref, d in REGISTRY.items()
        if d.spread_point_snapshot is not None and d.vendor == traded
    )


def _catalog():
    """カタログ（EA 名の注入元は本ファイルの関心外なので最小の束縛）。"""
    return symbol_spec_catalog.SymbolSpecCatalog(
        known_ea_names=lambda: (_INDEPENDENT_EA,)
    )


# --- 1. 何本・どの順で提供するか -----------------------------------------------------


def test_the_catalog_offers_the_legacy_series_first_and_the_traded_spread_series_after_it():
    """**並びの先頭は従来の系列**であり、取引している供給の気配幅系列はその後ろに在る。

    先頭が従来の ref であることは、先頭で profile を引いている共有フィクスチャと既存検定の
    前提そのものである（module docstring「なぜ順序を固定するのか」）。件数は焼き込まない
    （提供が何本になっても命題は同じ）。
    """
    # Arrange
    declared_spread = _ledger_spread_refs()

    # Act
    offered = [p.dataset for p in _catalog().datasets()]

    # Assert
    assert len(declared_spread) == 1, "取引している供給の気配幅系列が一意でない（対照が空虚になる）"
    assert declared_spread[0] != _LEGACY_REF   # 空振り防止（2 つは別の ref）
    assert offered[0] == _LEGACY_REF
    assert declared_spread[0] in offered[1:]


def test_each_series_points_at_its_own_ledger_declaration():
    """提供された系列はそれぞれ**自分の ref の台帳宣言**を指す（実体は別々である）。"""
    # Act
    profiles = _catalog().datasets()

    # Assert
    for profile in profiles:
        assert profile.data_path == str(whitelist()[profile.dataset])
    assert len({p.data_path for p in profiles}) == len(profiles)   # 空振り防止（別の実体）


# --- 2. 投入本文は増えない -----------------------------------------------------------


def test_the_dataset_label_is_not_one_of_the_submission_keys():
    """系列を分ける識別子（``dataset``）は投入 body の profile 由来キーに含まれない。"""
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
    assert len(bodies) > 1                                       # 空振り防止（比べる相手が在る）
    head = bodies[0]
    for body in bodies[1:]:
        assert body["data_path"] != head["data_path"]            # 空振り防止（別の実体）
        assert {k: v for k, v in body.items() if k != "data_path"} == {
            k: v for k, v in head.items() if k != "data_path"
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
    """``rows`` 行の実体を提供系列ごとに当て、`datasets()` 1 回の発行と相異なる実体を数える。

    実体の差し替えは**台帳の宣言**に当てる（ISSUE-533 段階 3 で提供が台帳由来になった）。
    気配幅の列を持つ実体と持たない実体を交互に当てるのは、列の有無が発行の数を変えない
    ことを併せて見るためである。
    """
    for index, ref in enumerate(sim_offered_refs()):
        header = MD6 if index % 2 == 0 else MD9
        point_entity_at(
            monkeypatch,
            ref,
            write_header(tmp_path, f"series_{rows}_{index}.csv", header, body_rows(header, rows)),
        )

    # ヘッダ読取は ISSUE-533 段階 2 で**問い自体が消えた**（建値基準を供給しなくなったので
    # 「その実体は気配幅を供給するか」を datasets() が問う理由が無い）。継ぎ目は残して
    # 「1 度も発行しないこと」を表明する——問いが戻ってきたら赤になる。
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
        "series": len(profiles),
    }


def test_the_reads_do_not_grow_with_the_number_of_series_or_the_data(monkeypatch, tmp_path):
    """3 継ぎ目すべてが 発行 − 相異なる実体の数 = 0 であり、データ行数でも増えない。

    提供がすべて同じ ``(サーバ, 銘柄)`` を指すため、スナップショットの相異なる実体は 1 である。
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
        assert measured["overrides"] == [None] * measured["series"]   # 決定論設定を供給しない
        assert measured["header"] == 0                 # 気配幅の問いを発行しない
        assert measured["range"] - measured["data_entities"] == 0
        assert measured["snapshot"] - measured["snapshot_entities"] == 0
    assert large["header"] == small["header"]       # 行数 1,000 倍でも発行は増えない
    assert large["range"] == small["range"]
    assert large["snapshot"] == small["snapshot"]
