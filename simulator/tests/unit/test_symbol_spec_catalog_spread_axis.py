"""カタログが実行条件（決定論設定の override）を**1 項目も供給しない**ことの固定。

ISSUE-533 段階 2。

何が変わったか:
    段階 8-D-1 では「その実体が気配幅を供給するか」で建値基準を載せるかを決めていた。
    その軸そのものが段階 2 で消えた——判定の瞬間を知っているのは戦略だけであり、値の
    出所は戦略の宣言ただ 1 つである。データ実体に判定の瞬間を決めさせる形は、**終値で
    判定する EA を気配幅つきの実体へ投げると run が始まらない**という害を生んでいた
    （実測 2026-09-25: 宣言 'close' と設定 'current_open' の食い違いで exit=2）。

    ファイル名に残る「spread_axis」は当時の軸の名である（改名は別途承認）。本ファイルが
    いま固定するのは、**どの形式・どの気配幅の有無でも供給が無い**ことである。

なぜ真理値表の形のまま残すか:
    「気配幅を供給する実体にだけ載せる」を復活させる変更は、気配幅を持たない実体だけを
    見る検定では 1 ビットも検出できない。7 通りの実体（気配幅あり 3・なし 4）で
    キーごと不在を表明することが、軸の復活を機械で赤にする唯一の形である。

計算量（CX-3）:
    `datasets()` が 1 実体について発行する読取を数える。段階 2 で**ヘッダ読取は問いごと
    消えた**（気配幅を問う理由が無くなった）ため、継ぎ目は残したまま「1 度も発行しない」
    を表明する。範囲読取は 発行 − 使用 = 0（使用 = プロファイルの数）であり、データ行数を
    変えても増えない。**回数そのものは焼き込まない。**

    **発行回数だけでは足りない**（ISSUE-511 段階 8-D-4・工程 5 レビュー 🟡-2 の是正）:
    発行回数は「1 回の読取の中で読む量が O(n) になる退化」を 1 ビットも検出しない。実測
    2026-09-25（HEAD 14fc9a13）: 範囲読取の後読み（終端から定数窓だけ読む 3 行）を先頭からの
    全読みへ退化させると、本ファイルと
    `simulator/tests/unit/test_symbol_spec_catalog_ledger_wiring.py` は **20 passed のまま
    素通しした**（出力の日付トークンも発行回数も変わらないため。壁時計は 0.46s → 10.91s だが
    **時間は表明しない**）。よって同じ規律で**配られた量**も数える
    （test_the_read_volume_does_not_grow_with_the_data）。

共有するテストヘルパは import して使う（同じものを手書き複製しない）:
    Test Spy（モジュール属性の継ぎ目） … `marketdata/tests/spread_series_fixture.py`
    Test Spy（開いた口と配られた量） … `simulator/tests/file_read_spy.py`
    ヘッダ定数・本文・書き出し … `simulator/tests/ohlc_header_fixtures.py`
    台帳の実体の差し替え … `simulator/tests/ledger_entity_fixtures.py`
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from marketdata.dataset_registry import sim_offered_refs
from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.sim_ui.adapter import symbol_spec_catalog
from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
from simulator.tests.file_read_spy import spy_file_reads
from simulator.tests.ledger_entity_fixtures import point_entity_at
from simulator.tests.ohlc_header_fixtures import (
    COMMA,
    COMMA_NO_SPREAD,
    GARBAGE,
    MD6,
    MD9,
    MT5_TAB,
    MT5_TAB_NO_SPREAD,
    body_rows,
    write_header,
)
from simulator.tests.tester_settings_engine_fixtures import (
    engine_binding,
    runnable_settings,
)

#: 実体のヘッダ → その実体が気配幅を供給するか。**答えは供給の有無を変えない**ことが
#: 本ファイルの主張なので、真偽どちらの実体も等しく並べる（気配幅あり 3・なし 4）。
_AXIS = {
    "marketdata_9_columns": (MD9, True),
    "marketdata_6_columns": (MD6, False),
    "comma_with_spread": (COMMA, True),
    "comma_without_spread": (COMMA_NO_SPREAD, False),
    "mt5_tab_with_spread": (MT5_TAB, True),
    "mt5_tab_without_spread": (MT5_TAB_NO_SPREAD, False),
    "unknown_form": (GARBAGE, False),
}

#: 気配幅に依存しない EA（N-17 を踏まずに写像層まで通すため）。
_INDEPENDENT_EA = "TC24051901"


def _catalog() -> SymbolSpecCatalog:
    """カタログ（EA 名の注入元は本ファイルの関心外なので最小の束縛）。"""
    return SymbolSpecCatalog(known_ea_names=lambda: (_INDEPENDENT_EA,))


def _entity(monkeypatch, tmp_path, name: str, header: str, body: str = "") -> str:
    """``header`` を持つデータ実体を書き、**先頭で提供される系列**の実体をそれへ差し替える。

    差し替えは台帳の宣言に当てる（ISSUE-533 段階 3 で提供が台帳由来になった）。先頭の系列に
    当てるのは、本ファイルが測るのが ``datasets()[0]``（＝従来の系列）だからである。
    """
    path = write_header(tmp_path, f"{name}.csv", header, body)
    point_entity_at(monkeypatch, sim_offered_refs()[0], path)
    return path


# --- 1. どの実体でも供給しない（軸の復活を赤にする）--------------------------------


@pytest.mark.parametrize("name", sorted(_AXIS))
def test_no_entity_supplies_an_execution_config(monkeypatch, tmp_path, name):
    """気配幅を供給する実体でも供給しない実体でも、override はキーごと不在である。"""
    # Arrange
    _entity(monkeypatch, tmp_path, name, _AXIS[name][0])

    # Act
    measured = _catalog().datasets()[0].config_overrides

    # Assert
    assert measured is None


def test_the_key_never_reaches_the_payload(monkeypatch, tmp_path):
    """キーが応答ペイロードにも現れない（不在が末端まで保たれる）。"""
    # Arrange（気配幅を供給する実体＝かつて供給していた側で測る）
    path = _entity(monkeypatch, tmp_path, "md9", MD9)

    # Act
    payload = _catalog().datasets()[0].to_dict()

    # Assert
    assert "config_overrides" not in payload
    assert payload["data_path"] == path   # 空振り防止（測った実体が差し替え先である）


def test_the_catalog_takes_no_entry_price_basis_binding():
    """建値基準の注入口そのものが無い（値の所有者を 2 つにしない）。"""
    assert "entry_price_basis" not in inspect.signature(
        SymbolSpecCatalog.__init__
    ).parameters


# --- 2. 末端（写像層の実効値）------------------------------------------------------


def _effective_basis(path: str, overrides: "dict | None") -> "str | None":
    """profile の override を束縛に載せて写像層を 1 回通し、実効の建値基準を返す。"""
    kwargs = to_interactor_kwargs(
        runnable_settings(Expert=f"{_INDEPENDENT_EA}.ex5"),
        engine_binding(data_path=path, config_overrides=overrides),
    )
    return kwargs["config_overrides"].get("entry_price_basis")


@pytest.mark.parametrize("name", sorted(_AXIS))
def test_the_settings_path_carries_no_entry_price_basis(monkeypatch, tmp_path, name):
    """実結線（合成根 → 写像層）でも建値基準は 1 つも運ばれない。

    カタログ単体の表明だけだと、合成根が別の値を差し込む形が残っても赤にならない。
    """
    # Arrange
    path = _entity(monkeypatch, tmp_path, name, _AXIS[name][0])
    profile = build_run_options_port().datasets()[0]

    # Act
    measured = _effective_basis(path, profile.config_overrides)

    # Assert
    assert profile.config_overrides is None
    assert measured is None


# --- 3. 計算量（CX-3）: 発行 − 使用 = 0・データ量で増えない -----------------------


def _measure(monkeypatch, tmp_path, rows: int) -> dict:
    """``rows`` 行の実体 1 つで `datasets()` を 1 回呼んだときの発行・使用・配られた量。

    キー: header（気配幅を問う発行。段階 2 で問いごと消えたので 0）・range（範囲読取の
    発行）・used（使用＝プロファイルの数）・overrides（供給された override）・
    delivered（配られた量の総和）・delivered_entity（差し替えた実体の分だけ）・
    seeks_entity（同じ実体への位置付けの発行）・size_entity（同じ実体の大きさ）。
    """
    path = _entity(monkeypatch, tmp_path, f"scale_{rows}", MD9, body_rows(MD9, rows))
    header_reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")
    range_reads = spy(monkeypatch, symbol_spec_catalog, "_csv_date_range")
    reads = spy_file_reads(monkeypatch)

    profiles = _catalog().datasets()

    return {
        "header": len(header_reads),
        "range": len(range_reads),
        "used": len(profiles),
        "overrides": profiles[0].config_overrides,
        "delivered": reads.delivered(),
        "delivered_entity": reads.delivered(path),
        "seeks_entity": reads.seek_count(path),
        "size_entity": Path(path).stat().st_size,
    }


def test_the_reads_do_not_grow_with_the_data(monkeypatch, tmp_path):
    """`datasets()` 1 回あたりの読取が、データ行数でも問いの数でも増えない。

    段階 2 で気配幅の問いが消えたので、ヘッダ読取は**1 度も発行されない**。問いが戻って
    くれば（＝実体に建値基準を決めさせる形が復活すれば）ここが赤になる。範囲読取は
    発行 − 使用 = 0 のままである。規模 2 点（5 行 / 5,000 行）で発行が等しいことも併せて
    表明する。**回数そのものは焼き込まない。**

    発行回数が等しくても**1 回で読む量**が規模で増える退化は残る。それを止める表明は
    test_the_read_volume_does_not_grow_with_the_data が持つ（8-D-4）。
    """
    # Act
    small = _measure(monkeypatch, tmp_path, 5)
    monkeypatch.undo()
    large = _measure(monkeypatch, tmp_path, 5_000)
    monkeypatch.undo()

    # Assert
    assert small["overrides"] is None               # 空振り防止（供給が無いことを見ている）
    assert large["overrides"] is None
    assert small["header"] == 0                     # 気配幅の問いを発行しない
    assert large["header"] == 0
    assert small["range"] - small["used"] == 0      # 範囲読取: 発行 − 使用 = 0
    assert large["range"] - large["used"] == 0
    assert large["range"] == small["range"]         # 行数 1,000 倍でも発行は増えない


def test_the_read_volume_does_not_grow_with_the_data(monkeypatch, tmp_path):
    """実体から**配らせる量**が、データ行数で増えない（末尾は後読みで足りる）。

    上の検定と同じ盲点をここでも塞ぐ（工程 5 レビュー 🟡-2）: 発行回数が等しいままでも、
    1 回の読取で実体を全走査する退化は通ってしまう。実測 2026-09-25（HEAD 14fc9a13）:
    範囲読取の後読みを先頭からの全読みへ退化させると、本ファイルと
    `simulator/tests/unit/test_symbol_spec_catalog_ledger_wiring.py` は **20 passed で
    素通しした**。**時間は表明しない**——数えるのは呼び手へ配られた量である。

    規模 2 点はどちらも**後読みの窓より大きい実体**にする（小さい方が窓に収まると、後読みは
    実体の全部を返し、等号が「実体の大きさ」を測ってしまう）。窓の大きさ（実装の定数）は
    書き写さず、「配られた量 < 実体の大きさ」の表明そのもので確かめる。規模は 2 桁変える。
    """
    # Act
    small = _measure(monkeypatch, tmp_path, 500)
    monkeypatch.undo()
    large = _measure(monkeypatch, tmp_path, 50_000)
    monkeypatch.undo()

    # Assert
    for measured in (small, large):
        assert measured["overrides"] is None                # 空振り防止（答えを使う）
        assert measured["delivered_entity"] > 0             # 生存確認（現に読んでいる）
        # 実体を走査していない＝配られた量が実体より小さい（＝後読みの窓に収まっていない）
        assert measured["delivered_entity"] < measured["size_entity"]
    assert large["size_entity"] > small["size_entity"]     # 空振り防止（2 点は別の規模）
    assert large["delivered_entity"] == small["delivered_entity"]
    assert large["delivered"] == small["delivered"]        # 他の実体を含めた総量も増えない
    assert large["seeks_entity"] == small["seeks_entity"]  # 行ごとに位置付け直さない
