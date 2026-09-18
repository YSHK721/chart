"""N-17 の述語を「その実体が気配幅を供給するか」へ置き換えた保証境界の検定。ISSUE-511 段階 8-C。

何が変わるか:
    述語は「形式 == marketdata」から「``supplies_spread(data_path)`` が False」になる。
    形式は気配幅の**代理変数**にすぎず、代理変数で測ると 9 列の新系列（気配幅あり）まで
    弾き、気配幅を持たない comma 形式は素通しする。問うべきことを直接問う形へ変える。

固定する不変条件（実測した発火表・下の `_BOUNDARY` が唯一の宣言）:

    marketdata 6 列   → 発火（従来どおり。現行の実行データセットがこれ）
    marketdata 9 列   → 非発火（境界解除の目的。段階 8-B で 3 本とも組めることは実測済み）
    MT5 TAB           → 非発火（突合フィクスチャ経路・指紋 A/B を保つ）
    comma + spread    → 非発火
    comma − spread    → **発火**（本段で新たに変わる向き）
    形式不明・不在    → 発火（読めない実体は遮断側へ縮退する）

    データ非供給（``data_path is None``）→ **非発火**。これは上の 6 行とは別の事実である
    ——「気配幅の無いデータで走らせる」ではなく「データを 1 行も読まない」であり、N-17 が
    防ぐ事象（spread=0 供給が実 MT5 と一致しない・H-4）が原理的に起こらない。根拠は規則 S:
    `apply_unsupported_rules` を呼ぶ非テストの呼び手は、変換層の写像入口
    `simulator/main/tester_settings/kwargs_mapper.effective_to_interactor_kwargs`
    ただ 1 つであり、その関数は規則 S の整合検査を**先に**呼ぶ。**行番号では指さない**
    ——行は編集で腐るため、指すのは関数名である（工程 5 レビュー 🔵-1）。唯一性と評価順は
    `simulator/tests/unit/test_unsupported_rules_run_after_rule_s.py` が構文木で固定する。
    その検査は simulator/main/engine_data_consistency.py へ委譲し、
    ``consumes_market_data(tick_model) != has_data`` を E-03 で Fail-Stop する**双条件**である。
    したがって N-17 が評価される時点で ``data_path is None`` は「バー系列を消費しない
    modelling（Model=3＝math calculations）である」と同値になる。

計算量（CX-2）:
    継ぎ目は形式判定と `supplies_spread` が共有するヘッダ読取
    （`ohlc_marketdata_csv._header_line`）。`apply_unsupported_rules` 1 回あたりの発行が、
    評価する宣言の数で増えないことを宣言数 2 点で表明する。**回数は焼き込まない**。

共有するテストヘルパは import して使う（同じものを手書き複製しない）:
    Test Spy … `marketdata/tests/spread_series_fixture.py`
    ヘッダ定数と書き出し … `simulator/tests/ohlc_header_fixtures.py`

    後者は工程 5 レビュー 🟡-1 の是正である。本ファイルと
    `simulator/tests/unit/test_ohlc_marketdata_csv_supplies_spread.py` が同じヘッダ定数と
    同じ書き出しヘルパを手書きで複製していた。とりわけ ``MD9`` は、本ファイルが
    「ヘッダ → 期待」の対（下の `_BOUNDARY`）で持つため、**気配幅なしへ腐っても期待値ごと
    辻褄が合い緑で通る**＝「marketdata 9 列は非発火」という主張が恒真式へ退化する配置
    だった。唯一源にした後は、同じ腐敗が `supplies_spread` 側の期待（``MD9`` は気配幅を
    供給する）とも食い違うため、片側の辻褄合わせでは緑にできない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.main.tester_settings import unsupported
from simulator.main.tester_settings.unsupported import (
    NOT_VIOLATED,
    RULES,
    SPREAD_DEPENDENT_EA_NAMES,
    UI_TRIGGER_NONE,
)
from simulator.tests.ohlc_header_fixtures import (
    COMMA,
    COMMA_NO_SPREAD,
    GARBAGE,
    MD6,
    MD9,
    MT5_TAB,
    write_header,
)
from simulator.tests.tester_settings_engine_fixtures import (
    engine_binding,
    runnable_settings,
)

_ROOT = Path(__file__).resolve().parents[3]
#: MT5 突合フィクスチャ（実 OANDA-Japan MT5 JP225 M1・読み取りのみ）。
_MT5_FIXTURE = (
    _ROOT / "simulator" / "tests" / "fixtures" / "mt5" / "ma_slope_jp225_202501"
    / "input" / "JP225_M1_202501.csv"
)
#: 現行の実行データセットの実体（6 列 marketdata・読み取りのみ）。
_FULL_CSV = _ROOT / "data" / "marketdata" / "jp225_m1.csv"

#: spread 非依存 EA（対照）。既存の N-17 検定と同じ既定 TC 経路の名前。
_INDEPENDENT_EA = "TC24051901"

#: 実体のヘッダ → N-17 が発火するか（**この表が発火の唯一の宣言**）。
_BOUNDARY = {
    "marketdata_6_columns": (MD6, True),
    "marketdata_9_columns": (MD9, False),
    "mt5_tab": (MT5_TAB, False),
    "comma_with_spread": (COMMA, False),
    "comma_without_spread": (COMMA_NO_SPREAD, True),
    "unknown_form": (GARBAGE, True),
}


def _violation(ea_name: str, data_path):
    """N-17 の判定式を 1 回発行し、その戻り値（違反値 / ``NOT_VIOLATED``）を返す。

    ``data_path`` を**文字列化しない**のが、simulator/tests/unit/
    test_unsupported_spread_dependency.py の同種のヘルパとの唯一の差である。本ファイルは「データ非供給（``None``）」を測るため、``None`` を
    ``"None"``（実在しないパス）へ潰すとその事実が測れなくなる。
    """
    rule = RULES["N-17"]
    settings = runnable_settings(Expert=f"{ea_name}.ex5")
    binding = engine_binding(
        data_path=data_path,
        known_ea_names=tuple(SPREAD_DEPENDENT_EA_NAMES) + (_INDEPENDENT_EA,),
    )
    return rule.detect(settings.effective(), binding)


def _fires(ea_name: str, data_path) -> bool:
    return _violation(ea_name, data_path) is not NOT_VIOLATED


# --- R-7 / R-8 / R-10: 3 本 × 実体の発火表 ---------------------------------------


@pytest.mark.parametrize("ea_name", sorted(SPREAD_DEPENDENT_EA_NAMES))
def test_the_boundary_follows_whether_the_entity_supplies_spread(ea_name, tmp_path):
    """spread 依存 EA 3 本すべてで、発火は気配幅の供給の有無だけで決まる。

    R-7（6 列で発火）・R-8（9 列で非発火）・R-10（MT5 TAB で非発火）を 1 つの表で測る。
    形式ではなく気配幅で決まっていることは、同じ形式で答えが割れる 2 組
    （marketdata 6 列 / 9 列・comma ±spread）が示す。
    """
    # Arrange
    entities = {
        name: write_header(tmp_path, f"{name}.csv", header)
        for name, (header, _expected) in _BOUNDARY.items()
    }
    # Act
    measured = {name: _fires(ea_name, path) for name, path in entities.items()}
    # Assert
    assert measured == {
        name: expected for name, (_header, expected) in _BOUNDARY.items()
    }


@pytest.mark.parametrize("ea_name", sorted(SPREAD_DEPENDENT_EA_NAMES))
def test_the_violation_value_is_the_ea_name(ea_name, tmp_path):
    """発火時の違反値は EA 名である（送出側が例外 context の value 欄に載せる）。"""
    # Arrange
    path = write_header(tmp_path, "md6.csv", MD6)
    # Act / Assert
    assert _violation(ea_name, path) == ea_name


def test_an_absent_path_fires(tmp_path):
    """実在しないパスは遮断側へ縮退する（読めない実体を通さない）。"""
    assert _fires("MA_Slope_EA", str(tmp_path / "no_such.csv")) is True


# --- R-9: 対照（spread 非依存 EA は同じ実体で 1 つも発火しない）--------------------


def test_a_spread_independent_ea_never_fires_on_any_entity(tmp_path):
    """spread 非依存 EA は、発火側の実体でも 1 つも発火しない。

    空振り防止の対照。これが無いと「EA 名の判定が壊れて全部発火」でも上の表が緑になる。
    """
    # Arrange
    entities = [
        write_header(tmp_path, f"{name}.csv", header)
        for name, (header, _expected) in _BOUNDARY.items()
    ]
    # Act
    measured = {path: _fires(_INDEPENDENT_EA, path) for path in entities}
    # Assert
    assert set(measured.values()) == {False}


# --- データ非供給（規則 S）は N-17 の対象外 ---------------------------------------


@pytest.mark.parametrize("ea_name", sorted(SPREAD_DEPENDENT_EA_NAMES))
def test_a_run_that_supplies_no_data_at_all_does_not_fire(ea_name):
    """``data_path is None``（`MATH_CALCULATIONS`）では発火しない。

    「気配幅の無いデータで走らせる」ことと「データを 1 行も読まない」ことは別の事実で
    あり、後者では N-17 が防ぐ事象（spread=0 供給・H-4）が原理的に起こらない。同値性の
    根拠は規則 S（simulator/main/engine_data_consistency.py の双条件）で、N-17 より先に
    評価される。

    素朴な単純置換（``not supplies_spread(None)`` → True＝発火）では、`Model=3` の run が
    exit 0 から exit 2 へ変わる。**出力が正しいまま壊れるのではなく、完走していた run が
    止まる**ため、ここを測らないと段階 8-C の通過条件「実行データを変えていないので全 run
    の出力が byte 不変」が破れる（実測 2026-09-18・本作業ツリー: 単純置換を一時適用して
    `simulator/sim_ui/tests` を全件走らせ、赤になった検定を数えた——1,219 件中
    `test_run_job_settings.py::test_Model3はデータ非供給で完走し建玉0になる` の 1 件）。
    """
    assert _fires(ea_name, None) is False


# --- R-11: UI 束縛の宣言は変わらない ----------------------------------------------


def test_the_ui_binding_still_declares_that_raw_tokens_cannot_decide():
    """生トークンでは判定できないという事実は述語を変えても変わらない。

    判定材料はデータ実体のヘッダであり、`.ini` の値ではない。
    """
    ui = RULES["N-17"].ui
    assert ui is not None
    assert ui.mode == UI_TRIGGER_NONE
    assert ui.keys == ("Expert",)


# --- R-12: 実データでは従来どおり発火し続ける -------------------------------------


@pytest.mark.skipif(
    not _FULL_CSV.is_file(), reason="全期間データ実体（data/marketdata/jp225_m1.csv）が無い環境"
)
@pytest.mark.parametrize("ea_name", sorted(SPREAD_DEPENDENT_EA_NAMES))
def test_the_real_execution_dataset_still_fires(ea_name):
    """現行の実行データセット（6 列）では従来どおり弾き続ける（読み取りのみ）。

    実行データの差し替えは段階 8-D であり本段では行わない。ここが緑のままであることが
    「境界を開けたが実行データは動かしていない」ことの機械の証拠になる。
    """
    # Arrange: 前提の実測（この実体が気配幅の列を持たないこと）
    assert ohlc_marketdata_csv.supplies_spread(str(_FULL_CSV)) is False
    # Act / Assert
    assert _fires(ea_name, str(_FULL_CSV)) is True


@pytest.mark.skipif(not _MT5_FIXTURE.is_file(), reason="MT5 突合フィクスチャが無い環境")
@pytest.mark.parametrize("ea_name", sorted(SPREAD_DEPENDENT_EA_NAMES))
def test_the_real_mt5_fixture_still_does_not_fire(ea_name):
    """突合フィクスチャの実体（`<SPREAD>` を持つ）では発火しない＝指紋経路を保つ。"""
    # Arrange: 前提の実測
    assert ohlc_marketdata_csv.supplies_spread(str(_MT5_FIXTURE)) is True
    # Act / Assert
    assert _fires(ea_name, str(_MT5_FIXTURE)) is False


# --- CX-2: 計算量（発行 − 使用 = 0・宣言の数で増えない）---------------------------


def _extra_declarations(count: int) -> tuple:
    """評価順の末尾に**発火しない**宣言を ``count`` 件足した `RUN_REQUEST_RULES`。

    足すのは N-17 以外の既存宣言（`RULES["N-02"]`＝最適化）である。N-17 を複製すると
    「判定そのものが増えた」ことになり、測りたい量（**他の**宣言が増えても N-17 の発行が
    増えないこと）と別のものを測ってしまう。
    """
    return unsupported.RUN_REQUEST_RULES + (RULES["N-02"],) * count


def _issued_and_used(monkeypatch, data_path, extra: int) -> "tuple[int, int]":
    """宣言を ``extra`` 件足した状態で `apply_unsupported_rules` を 1 回適用する。

    発行 = ヘッダ読取（形式判定と `supplies_spread` が共有する継ぎ目）。
    使用 = その適用で N-17 の判定が答えを使った回数（宣言表の N-17 の件数 × 適用 1 回）。
    """
    settings = runnable_settings(Expert="MA_Slope_EA.ex5")
    binding = engine_binding(
        data_path=data_path,
        known_ea_names=tuple(SPREAD_DEPENDENT_EA_NAMES) + (_INDEPENDENT_EA,),
    )
    declarations = _extra_declarations(extra)
    monkeypatch.setattr(unsupported, "RUN_REQUEST_RULES", declarations)
    reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")

    unsupported.apply_unsupported_rules(settings.effective(), binding)

    used = sum(1 for rule in declarations if rule.unsupported_id == "N-17")
    return len(reads), used


def test_the_header_reads_do_not_grow_with_the_number_of_declarations(
    monkeypatch, tmp_path
):
    """`apply_unsupported_rules` 1 回あたりの読取が、評価する宣言の数で増えない。

    規模 2 点（宣言を 0 件足した表と 5 件足した表）で、発行 − 使用 = 0 と、両点で発行が
    等しいことを表明する。**回数そのものは焼き込まない**——焼き込むと、いま何回読んで
    いるかが仕様へ昇格し、無駄を減らす改善まで赤にする。
    """
    # Arrange: 非発火の実体を使う（発火すると最初の 1 件で打ち切られ、後続の宣言が
    # 評価されない＝「宣言の数で増えない」ことの検証が空振りする）。
    path = write_header(tmp_path, "md9.csv", MD9)

    # Act
    few_issued, few_used = _issued_and_used(monkeypatch, path, extra=0)
    monkeypatch.undo()
    many_issued, many_used = _issued_and_used(monkeypatch, path, extra=5)
    monkeypatch.undo()

    # Assert
    assert (few_used, many_used) == (1, 1)        # 空振り防止（N-17 が評価されている）
    assert few_issued - few_used == 0             # 発行 − 使用 = 0
    assert many_issued - many_used == 0
    assert many_issued == few_issued              # 宣言が増えても発行は増えない
