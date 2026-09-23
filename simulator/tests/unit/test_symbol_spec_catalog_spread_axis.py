"""実行条件（決定論設定の override）の軸を「形式」から「気配幅の供給」へ移した検定。

ISSUE-511 段階 8-D-1。

何が変わるか:
    カタログが override を供給するかの判定は「形式 == mt5_tab」から
    「その実体が気配幅を供給するか」になる。形式は気配幅の代理変数にすぎず、代理で測ると
    気配幅を持つ marketdata 9 列（段階 2 の新系列）へ override を供給せず、気配幅を持たない
    タブ区切りの実体には供給してしまう。同じ置換を保証境界 N-17 について行ったのが段階 8-C
    であり（`simulator/tests/unit/test_unsupported_n17_supplies_spread_boundary.py`）、
    本ファイルは実行条件の側について同じ軸を固定する。

値の所有者:
    建値基準の値はカタログが持たない。単一ソースは `ENTRY_PRICE_BASIS` であり、
    `simulator.sim_ui.main.composition_root_jobs` が注入する（known_ea_names と同じ様式＝
    既定束縛を置かない）。カタログが持ってよいのは「実体が気配幅を供給するか」という事実
    だけである。注入であることは、エンジンの語彙でない番兵 `_INJECTED` を注入して、それが
    そのまま供給されることで実証する（カタログ内のリテラルでは番兵は出てこない）。

キー不在が要る理由（退行の罠）:
    S が偽のとき **キーごと不在**にする。写像層の override 合成は
    ``setdefault`` で補うため、``binding.config_overrides`` に載っているキーの方が勝つ。
    ここで "close" を明示すると、いま `ENTRY_PRICE_BASIS` で走っている settings 経路が
    反転して既存の実行結果が動く。その反転を捕まえるのが
    test_a_spreadless_dataset_keeps_the_settings_path_default である。

Red と回帰ガードの別（成功テスト先行を Red と称さない）:
    * 真の Red … 建値基準の注入と気配幅の軸を要求する検定。実装前は
      `SymbolSpecCatalog` が当該引数を受け取らないため `TypeError` で落ちる。
    * 回帰ガード … S が偽のときの settings 経路の実効値を固定する検定。**是正前後の
      どちらでも緑**であり Red ではない。その検出力は「"close" を供給する版へ差し替える
      変異」で実測する（実測値は本作業の報告に記す）。

計算量（CX-3）:
    継ぎ目は `datasets()` が 1 実体について発行するヘッダ読取
    （`ohlc_marketdata_csv._header_line`）と、カタログ側の範囲読取である。
    どちらも 発行 − 使用 = 0（使用 = プロファイルの数）であり、データ行数を変えても
    増えないことを規模 2 点で表明する。**回数そのものは焼き込まない**。

共有するテストヘルパは import して使う（同じものを手書き複製しない）:
    Test Spy … `marketdata/tests/spread_series_fixture.py`
    ヘッダ定数・本文・書き出し … `simulator/tests/ohlc_header_fixtures.py`
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.main.tester_settings.kwargs_mapper import (
    ENTRY_PRICE_BASIS,
    to_interactor_kwargs,
)
from simulator.sim_ui.adapter import symbol_spec_catalog
from simulator.sim_ui.adapter.symbol_spec_catalog import SymbolSpecCatalog
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
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

#: 実体のヘッダ → その実体が気配幅を供給するか（**この表が軸の唯一の宣言**）。
#: 同じ形式で答えが割れる 3 組（marketdata 6 列 / 9 列・comma ±spread・MT5 TAB ±SPREAD）が、
#: 判定が形式ではなく気配幅の供給で決まっていることを示す。
_AXIS = {
    "marketdata_9_columns": (MD9, True),
    "marketdata_6_columns": (MD6, False),
    "comma_with_spread": (COMMA, True),
    "comma_without_spread": (COMMA_NO_SPREAD, False),
    "mt5_tab_with_spread": (MT5_TAB, True),
    "mt5_tab_without_spread": (MT5_TAB_NO_SPREAD, False),
    "unknown_form": (GARBAGE, False),
}

#: 注入された基準値であることを示す番兵。**エンジンの語彙ではない**——カタログが値の
#: リテラルを持っていればこの文字列は出てこないため、注入の実証になる。
_INJECTED = "__injected_basis__"

#: 注入の差し替えが効くことを見るための別値（写像層まで届くかを測る positive control）。
#: 既存の優先順位検定（binding が勝つ）と同じ語を使う。
_ALTERNATIVE = "current_close"

#: 気配幅に依存しない EA（N-17 を踏まずに写像層まで通すため）。
_INDEPENDENT_EA = "TC24051901"


def _expected_override(supplies: bool, basis: str) -> "dict | None":
    """気配幅を供給する実体だけが override を受け取る（供給しなければ**キーごと不在**）。"""
    return {"entry_price_basis": basis} if supplies else None


def _catalog(basis: str) -> SymbolSpecCatalog:
    """基準値を注入したカタログ（EA 名の注入元は本ファイルの関心外なので最小の束縛）。"""
    return SymbolSpecCatalog(
        known_ea_names=lambda: (_INDEPENDENT_EA,), entry_price_basis=basis
    )


def _entity(monkeypatch, tmp_path, name: str, header: str, body: str = "") -> str:
    """``header`` を持つデータ実体を書き、カタログの実体をそれへ差し替える。"""
    path = write_header(tmp_path, f"{name}.csv", header, body)
    monkeypatch.setattr(symbol_spec_catalog, "_JP225_DATA_CSV", Path(path))
    return path


# --- 1. 真理値表（S = 気配幅を供給するか）----------------------------------------


@pytest.mark.parametrize("name", sorted(_AXIS))
def test_the_override_follows_whether_the_entity_supplies_spread(
    monkeypatch, tmp_path, name
):
    """供給される override は、形式ではなく気配幅の供給だけで決まる。

    気配幅を供給する実体には注入された基準値が入り、供給しない実体には**キーが無い**。
    """
    # Arrange
    header, supplies = _AXIS[name]
    _entity(monkeypatch, tmp_path, name, header)

    # Act
    measured = _catalog(_INJECTED).datasets()[0].config_overrides

    # Assert
    assert measured == _expected_override(supplies, _INJECTED)


def test_an_entity_without_spread_leaves_no_key_in_the_payload(monkeypatch, tmp_path):
    """S が偽の実体では、キーが応答ペイロードにも現れない（不在が末端まで保たれる）。"""
    # Arrange
    path = _entity(monkeypatch, tmp_path, "md6", MD6)

    # Act
    payload = _catalog(_INJECTED).datasets()[0].to_dict()

    # Assert
    assert "config_overrides" not in payload
    assert payload["data_path"] == path   # 空振り防止（測った実体が差し替え先である）


# --- 2. 値の所有者（カタログは事実だけを持ち、値は注入で受ける）--------------------


def test_the_entry_price_basis_has_no_default_binding():
    """基準値の注入は必須引数である（既定束縛を置くと値の所有者が 2 つになる）。"""
    parameter = inspect.signature(SymbolSpecCatalog.__init__).parameters[
        "entry_price_basis"
    ]
    assert parameter.default is inspect.Parameter.empty


def test_the_composition_root_injects_the_engine_value(monkeypatch, tmp_path):
    """実際に結線された注入元は写像層の単一ソースである（カタログのリテラルではない）。"""
    # Arrange
    _entity(monkeypatch, tmp_path, "md9", MD9)

    # Act
    measured = build_run_options_port().datasets()[0].config_overrides

    # Assert
    assert measured == {"entry_price_basis": ENTRY_PRICE_BASIS}


# --- 3. 回帰ガード（負の対照つき）: settings 経路の実効値 --------------------------
#
# 下の 2 件は是正前後のどちらでも緑であり **Red ではない**（成功テスト先行を Red と
# 称さない）。検出力は変異で実測する: S が偽の側へ "close" を供給する版へ差し替えると
# test_a_spreadless_dataset_keeps_the_settings_path_default が落ちる。


def _effective_basis(path: str, overrides: "dict | None") -> str:
    """profile の override を束縛に載せて写像層を 1 回通し、実効の建値基準を返す。"""
    kwargs = to_interactor_kwargs(
        runnable_settings(Expert=f"{_INDEPENDENT_EA}.ex5"),
        engine_binding(data_path=path, config_overrides=overrides),
    )
    return kwargs["config_overrides"]["entry_price_basis"]


def test_a_spreadless_dataset_keeps_the_settings_path_default(monkeypatch, tmp_path):
    """気配幅を供給しない実体では、settings 経路の実効値が単一ソースのままである。

    カタログがキーを供給しないため、写像層の ``setdefault`` が補う値がそのまま実効値に
    なる。カタログが "close" を明示すると `binding.config_overrides` が勝って反転する
    ——その反転をここが捕まえる（負の対照）。
    """
    # Arrange
    path = _entity(monkeypatch, tmp_path, "md6", MD6)
    profile = build_run_options_port().datasets()[0]

    # Act
    measured = _effective_basis(path, profile.config_overrides)

    # Assert
    assert profile.config_overrides is None     # キーごと不在
    assert measured == ENTRY_PRICE_BASIS


def test_the_injected_basis_reaches_the_settings_path(monkeypatch, tmp_path):
    """気配幅を供給する実体では、注入された基準値が写像層の実効値になる（positive control）。

    これが無いと、上の回帰ガードは「カタログが何も供給しない」ことしか見ておらず、
    供給した値が届くかを 1 つも測らないまま緑になる。
    """
    # Arrange
    path = _entity(monkeypatch, tmp_path, "md9", MD9)
    profile = _catalog(_ALTERNATIVE).datasets()[0]

    # Act
    measured = _effective_basis(path, profile.config_overrides)

    # Assert
    assert profile.config_overrides == {"entry_price_basis": _ALTERNATIVE}
    assert measured == _ALTERNATIVE


# --- 4. 計算量（CX-3）: 発行 − 使用 = 0・データ量で増えない -----------------------


def _issued_and_used(monkeypatch, tmp_path, rows: int) -> tuple:
    """``rows`` 行の実体 1 つで `datasets()` を 1 回呼んだときの発行と使用。

    戻り値は（ヘッダ読取の発行, 範囲読取の発行, 使用＝プロファイルの数, 供給された override）。
    """
    path = _entity(monkeypatch, tmp_path, f"scale_{rows}", MD9, body_rows(MD9, rows))
    header_reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")
    range_reads = spy(monkeypatch, symbol_spec_catalog, "_csv_date_range")

    profiles = _catalog(_INJECTED).datasets()

    del path
    return (
        len(header_reads),
        len(range_reads),
        len(profiles),
        profiles[0].config_overrides,
    )


def test_the_reads_do_not_grow_with_the_data(monkeypatch, tmp_path):
    """`datasets()` 1 回あたりの読取が、データ行数でも問いの数でも増えない。

    形式を問うために 1 回・気配幅を問うためにもう 1 回読む形は、出力が 1 ビットも変わら
    ないため状態検証では落ちない。ここが唯一その無駄を止める。規模 2 点（5 行 / 5,000 行）
    で発行が等しいことも併せて表明する。**回数そのものは焼き込まない**。
    """
    # Act
    small = _issued_and_used(monkeypatch, tmp_path, 5)
    monkeypatch.undo()
    large = _issued_and_used(monkeypatch, tmp_path, 5_000)
    monkeypatch.undo()

    # Assert
    assert small[3] == {"entry_price_basis": _INJECTED}   # 空振り防止（答えを使っている）
    assert large[3] == small[3]
    assert small[0] - small[2] == 0     # ヘッダ読取: 発行 − 使用 = 0
    assert large[0] - large[2] == 0
    assert small[1] - small[2] == 0     # 範囲読取: 発行 − 使用 = 0
    assert large[1] - large[2] == 0
    assert large[0] == small[0]         # 行数 1,000 倍でも発行は増えない
