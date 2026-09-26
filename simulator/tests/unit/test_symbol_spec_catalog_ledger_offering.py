"""sim の選択肢を**台帳の宣言**から導いたことの検定（ISSUE-533 段階 3・依頼者承認 2026-09-26）。

何を固定するか:
    実行指示フォームが選べる系列（`GET /run-options` の datasets）は、台帳
    `marketdata/dataset_registry.py` が**提供すると宣言した ref ちょうどそれ**であり、
    並びは台帳の宣言順である。カタログは手書きの対応表を持たない。

是正前に何が偽だったか（実測 2026-09-26・本作業ツリー・HEAD 1ab3369f）:
    台帳には気配幅つきの新系列が宣言済みで実体も生成済み（407 万行）だったのに、
    ``GET /sim/run-options`` は **2 件しか返さなかった**。カタログの提供一覧が手書きの
    2 要素タプルだったためである。台帳に足しても UI へ届かない——列挙は必ず取り残しを生む。

Red と回帰ガードの別（成功テスト先行を Red と称さない）:
    * 真の Red … test_the_spread_series_of_the_other_supply_is_offered
      （台帳が宣言済みの新系列が応答に現れることを要求する。是正前は 2 件なので落ちる）。
    * 真の Red … test_a_series_the_ledger_newly_declares_appears_in_the_response
      （**宣言をそのまま写した**系列を台帳へ足すと応答に現れることを要求する。是正前は
      手書きの対応表なので届かない＝落ちる）。綴りも新しい欄の名も使わない——「すでに提供
      すると宣言されている系列の宣言を、実体だけ変えて写す」形で足す。
    * 真の Red … test_the_offered_series_are_exactly_the_ones_the_ledger_declares_for_sim
      （提供の集合と並びが台帳の宣言と一致することを要求する。是正前は欄そのものが無い）。
    * 真の Red … test_the_reads_do_not_grow_with_the_number_of_offered_series
      （系列の数を 2 点変えて測る。是正前は足しても提供が増えないので空振り防止で落ちる）。
    * 回帰ガード … test_the_spreadless_series_stays_at_the_head_of_the_offering
      （是正前後のどちらでも緑。検出力は変異で実測する＝本作業の報告に記す）。

なぜ並びの先頭を固定するのか（**実測された危険**・2026-09-25）:
    共有フィクスチャと既存検定は profile を「先頭」または「銘柄一致の先頭」で引いている。
    先頭が入れ替わると、これらは**赤にならずに別系列で走る**（同じ 10 ファイルが 103.67 秒
    から 29 分超へ伸びたまま終わらない。失敗ではなく実行対象の入替としてのみ現れる）。
    2 本のときの表明は `simulator/tests/unit/test_symbol_spec_catalog_second_series.py` が
    持つ。ここが固定するのは、**提供が 3 本以上になっても同じ規律が成り立つ**ことである。
    先頭の同定に綴りを使わない——台帳の宣言で「気配幅を宣言していない唯一の系列」が従来の
    系列である。

対照（提供しないと宣言した ref が出ないこと）はここに二重に書かない:
    `simulator/tests/unit/test_symbol_spec_catalog_ledger_wiring.py` の
    test_a_ref_the_ledger_does_not_offer_to_sim_stays_out が持つ（宣言を欠いた ref で
    止まることも同ファイル）。ここは実在の台帳について「提供しないと宣言した ref が
    現に在る」ことだけを空振り防止として見る。

計算量（CX・絶対命令 2026-08-28）:
    継ぎ目は 3 つ——ヘッダ読取（ohlc_marketdata_csv の _header_line）・範囲読取（カタログの
    _csv_date_range）・供給元スナップショット読取（load_snapshot）。表明は **発行 − 相異なる
    実体の数 = 0** と、**系列の数を増やしても供給元の読取が増えないこと**（規模 2 点は
    「宣言された提供」と「それに 1 本足した提供」）。**回数そのものは焼き込まない**。

共有するテストヘルパは import して使う（同じものを手書き複製しない）:
    Test Spy … `marketdata/tests/spread_series_fixture.py`
    台帳の実体の差し替え … `simulator/tests/ledger_entity_fixtures.py`
    ヘッダ定数・本文・書き出し … `simulator/tests/ohlc_header_fixtures.py`

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from marketdata.dataset_registry import DEFAULT_DATASET_REF, REGISTRY
from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.sim_ui.adapter import symbol_spec_catalog
from simulator.sim_ui.adapter.run_options_api_controller import RunOptionsApiController
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
from simulator.sim_ui.usecase.list_run_options import ListRunOptionsInteractor
from simulator.tests.ledger_entity_fixtures import point_entity_at
from simulator.tests.ohlc_header_fixtures import MD6, body_rows, write_header

#: 台帳へ一時的に足す系列の ref の頭（既存のどの ref とも衝突しない綴り）。
_PROBE = "zz_probe_series"


def _offered() -> "list[str]":
    """いま提供されている dataset ref の並び（カタログの答えをそのまま観測する）。"""
    return [p.dataset for p in build_run_options_port().datasets()]


def _response_datasets() -> "list[dict]":
    """`GET /run-options` 相当の応答の datasets（実物の UC と変換器を通す）。

    HTTP の面は `simulator/sim_ui/tests/integration/test_serve_sim_run_options.py` が
    持つ。ここは同じ UC・同じ変換器を直に通して、応答の中身だけを見る。
    """
    controller = RunOptionsApiController(
        options=ListRunOptionsInteractor(port=build_run_options_port())
    )
    response = controller.list()
    assert response.status == 200
    assert response.payload["ok"] is True
    return response.payload["datasets"]


def _spreadless(refs) -> "list[str]":
    """``refs`` のうち台帳が**気配幅を宣言していない**もの（＝従来の系列）。"""
    return [ref for ref in refs if REGISTRY[ref].spread_point_snapshot is None]


def _spread_ref_of_the_other_supply() -> str:
    """気配幅を宣言した ref のうち、**取引している供給ではない**側（＝本段の新系列）。

    綴りを書き写さない: 取引している供給は既定 datasetRef のベンダであり、その補集合が
    もう一方の供給である。取引している側の気配幅系列を引く対の口は
    `simulator/tests/unit/test_symbol_spec_catalog_second_series.py` が持つ。
    """
    traded = REGISTRY[DEFAULT_DATASET_REF].vendor
    others = [
        ref
        for ref, d in REGISTRY.items()
        if d.spread_point_snapshot is not None and d.vendor != traded
    ]
    assert len(others) == 1, f"取引していない供給の気配幅系列が一意でない: {others}"
    return others[0]


def _clone_of_an_offered_series(monkeypatch, tmp_path, ref: str) -> Path:
    """**提供すると宣言済みの系列の宣言**を写し、実体だけ変えて ``ref`` として台帳へ足す。

    新しい欄の名も既存 ref の綴りも使わない（宣言を丸ごと写すので、提供の宣言も一緒に
    付いてくる）。カタログが台帳の宣言から導いていれば、これは選択肢に現れる。
    """
    source = REGISTRY[_offered()[0]]
    entity = Path(write_header(tmp_path, f"{ref}.csv", MD6, body_rows(MD6, 5)))
    monkeypatch.setitem(REGISTRY, ref, replace(source, path=entity))
    return entity


# --- 1. 提供の集合と並びは台帳の宣言そのもの ----------------------------------------


def test_the_offered_series_are_exactly_the_ones_the_ledger_declares_for_sim():
    """提供する ref の並びは、台帳が sim へ提供すると宣言した ref の宣言順と一致する。

    手書きの対応表であれば、台帳の宣言と一致する保証は 1 つも無い（現に段階 3 の直前まで
    一致していなかった）。
    """
    # Arrange
    declared = [ref for ref, d in REGISTRY.items() if d.sim_offered]
    withheld = [ref for ref, d in REGISTRY.items() if d.sim_offered is False]

    # Act
    offered = _offered()

    # Assert
    assert withheld, "提供しないと宣言した ref が台帳に 1 件も無い（対照が空虚になる）"
    assert len(offered) < len(REGISTRY)   # 台帳を丸ごと出していない
    assert offered == declared


def test_a_series_the_ledger_newly_declares_appears_in_the_response(monkeypatch, tmp_path):
    """台帳へ系列を足すと（宣言を写して）、その ref が run-options 相当の応答に現れる。

    これが本段の取り残しの再発防止である。カタログが手書きの対応表を持つ限り、台帳へ
    足した系列はここへ届かない。
    """
    # Arrange
    entity = _clone_of_an_offered_series(monkeypatch, tmp_path, _PROBE)

    # Act
    datasets = _response_datasets()

    # Assert
    added = [d for d in datasets if d["dataset"] == _PROBE]
    assert len(added) == 1, f"足した系列が応答に無い: {[d['dataset'] for d in datasets]}"
    assert added[0]["data_path"] == str(entity)   # 空振り防止（足した宣言の実体である）


def test_the_spread_series_of_the_other_supply_is_offered():
    """もう一方の供給の**気配幅つき系列**（本段の新系列）が応答に現れる。

    台帳の宣言と実体は本段の前から在り、届いていなかったのは選択肢だけである。
    """
    # Arrange
    target = _spread_ref_of_the_other_supply()

    # Act
    datasets = _response_datasets()

    # Assert
    offered = [d["dataset"] for d in datasets]
    assert target in offered, f"新系列 {target} が選択肢に無い: {offered}"
    entry = [d for d in datasets if d["dataset"] == target][0]
    assert entry["data_path"] == str(REGISTRY[target].path)   # 台帳の宣言そのもの
    assert Path(entry["data_path"]).is_file()                 # 実体が現に在る


# --- 2. 並びの規律（3 本以上でも先頭は従来の系列）------------------------------------


def test_the_spreadless_series_stays_at_the_head_of_the_offering():
    """提供が 3 本以上になっても、先頭は気配幅を宣言していない唯一の系列のままである。

    先頭で profile を引いている共有フィクスチャと既存検定の前提そのものである
    （module docstring「なぜ並びの先頭を固定するのか」）。件数は焼き込まない。
    """
    # Act
    offered = _offered()

    # Assert
    spreadless = _spreadless(offered)
    assert len(spreadless) == 1, f"気配幅を宣言していない提供が一意でない: {spreadless}"
    assert offered[0] == spreadless[0]
    assert len(offered) > len(spreadless)   # 空振り防止（気配幅つきの提供が後続に在る）


# --- 3. 計算量（CX）: 系列が増えても実体の読取は出力の分だけ -------------------------


def _measure(monkeypatch, tmp_path, extra: int) -> dict:
    """提供系列の実体を小さな合成 CSV へ向け、``extra`` 本足した状態で 1 回測る。"""
    for index, ref in enumerate(_offered()):
        point_entity_at(
            monkeypatch,
            ref,
            write_header(tmp_path, f"offered_{extra}_{index}.csv", MD6, body_rows(MD6, 5)),
        )
    for serial in range(extra):
        _clone_of_an_offered_series(monkeypatch, tmp_path, f"{_PROBE}_{extra}_{serial}")

    header_reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")
    range_reads = spy(monkeypatch, symbol_spec_catalog, "_csv_date_range")
    snapshot_reads = spy(monkeypatch, symbol_spec_catalog, "load_snapshot")

    profiles = build_run_options_port().datasets()
    return {
        "header": len(header_reads),
        "range": len(range_reads),
        "snapshot": len(snapshot_reads),
        "used": len(profiles),
        # 相異なる実体: データ側は別々の CSV、スナップショット側は読んだ組（サーバ, 銘柄）。
        "data_entities": len({p.data_path for p in profiles}),
        "snapshot_entities": len(set(snapshot_reads)),
    }


def test_the_reads_do_not_grow_with_the_number_of_offered_series(monkeypatch, tmp_path):
    """系列を 1 本足しても、3 継ぎ目は 発行 − 相異なる実体の数 = 0 のままである。

    提供が増えれば範囲読取はその分だけ増える（出力に使う）。増えてはならないのは供給元
    スナップショットの読取であり、系列ごとに読み直す形は出力を 1 ビットも変えないため
    状態検証では落ちない。**回数そのものは焼き込まない**。
    """
    # Act
    declared = _measure(monkeypatch, tmp_path, 0)
    monkeypatch.undo()
    with_extra = _measure(monkeypatch, tmp_path, 1)
    monkeypatch.undo()

    # Assert
    assert with_extra["used"] > declared["used"]     # 空振り防止（系列は現に増えた）
    for measured in (declared, with_extra):
        assert measured["snapshot"] >= 1             # 空振り防止（現に読んでいる）
        assert measured["data_entities"] > 1         # 空振り防止（実体は複数ある）
        assert measured["header"] == 0               # 気配幅の問いを発行しない（段階 2）
        assert measured["range"] - measured["data_entities"] == 0
        assert measured["snapshot"] - measured["snapshot_entities"] == 0
    # 系列が増えても供給元の読取は増えない（同じ (サーバ, 銘柄) を読み直さない）
    assert with_extra["snapshot"] == declared["snapshot"]
    assert with_extra["header"] == declared["header"]
