"""同じティックから複数系列を導く案内（ISSUE-511 段階 8-D-2b の段 2）。

用語（初出定義）:
    案内（plan）
        ＝ ``marketdata.tick_m1.series_plan`` が返す :class:`~marketdata.tick_m1.SeriesPlan`。
          「同じティック木から導く datasetRef の組を、どの価格基準で畳み、どの ref が spread 列を
          持つか」を 1 つに決めた値である。案内を作らずに複数系列を書く経路を作らないための口。
    畳み（fold）
        ＝ ティック行 → 分バーの集約。唯一の畳み点は ``marketdata.tick_m1._fold_ticks``
          （公開の口の入口ではなく共通前段。入口で数えると委譲先の内部で 2 回畳む変異が
          1 回に見える・``marketdata/tests/test_mt5_rebuild_materialize.py`` のレビュー指摘 Y-1）。
    射影（projection）
        ＝ spread 付きで畳んだ結果から spread 列を落として得る、spread 無し系列の分バー。
          再計算ではない。
    発行 / 使用
        発行 ＝ 唯一の畳み点へ渡ったティック行数の合計。使用 ＝ 出力に使ったティック行数
          （入力の行数）。規約の形は「発行 − 使用 = 0」（絶対命令 2026-08-28）。

本検定が固定するもの:
  L-1 **射影の補題**（本段の関門）: 「spread 付きで畳んで spread 列を落とした結果」と
      「spread 無しで畳んだ結果」が **index・列名・dtype・全列の値**まで一致する。
      設計 D-1 はこの点を「コード読取と 09-14 の行数一致（1355 = 1355）」までしか根拠に
      持たず、値の一致は**未実測**と自ら記している（ISSUE-511 段階 8-D-2b・T-1）。行数だけを
      見る突合では、bid/ask 列の同乗が並べ替えや groupby の結果を動かす退行を素通しする。
  L-2 射影の補題の素材化版（``materialize_m1_day_for_series``＝外れ分除去・行選択の後）。
  CX-1 **畳みは 1 回だけ**: 系列数 1 → 2 で発行 − 使用 = 0 が保たれ、かつ発行が増えない。
       規模 2 点（分数 2 点 × 系列数 2 点）で固定する。**回数そのものは期待値に焼き込まない**
       （期待値は入力のティック行数から導く）。
  F-1 案内の Fail-Stop: 価格基準の食い違い・spread 宣言の食い違い・空の組・同じ ref の重複は
       名前付き例外 :class:`~marketdata.tick_m1.SeriesPlanConflict` で止まる。
  F-2 案内が受けた ref は 1 つも落ちない（出力の鍵が案内の並びと 1 対 1）。
  E-1 既存の口（``fold_ticks_for`` / ``materialize_m1_day``）の名前・シグネチャ・戻り値が不変で、
       1 要素の案内を通した結果と一致する。

本検定が固定しないもの（射程の明示）:
  - 呼び手（常駐・日中追記 m1_chain・日次再構築 rebuild）は本段では 1 バイトも変えない。案内を使って書く経路は
    段 3 以降の担当であり、本ファイルは案内と畳みの口だけを測る。
  - 台帳への宣言の投入（段階 7）。本ファイルは合成 ref を一時登録して測る。
  - 列形の照合そのもの（``tick_m1._checked_series``）。それは
    ``marketdata/tests/test_mt5_m1_chain_ledger_series.py`` の R-15 / R-15b が持つ。

書込はすべて ``tmp_path``（data_dir を必ず渡す）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from marketdata import symbol_spec_snapshot as sss
from marketdata import tick_m1
from spread_series_fixture import (
    LEDGER_BASIS,
    SNAPSHOT_PAIR,
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    register_tick_ref,
)

#: 合成 ref（spread 列を持つ / 持たない / 価格基準が違う / 別の宣言）。台帳へ一時登録する。
_SPREAD_REF = "zz_plan_spread"
_PLAIN_REF = "zz_plan_plain"
_OTHER_BASIS_REF = "zz_plan_mid"
_OTHER_SNAPSHOT_REF = "zz_plan_other_snapshot"
#: 別の宣言（同じサーバの別銘柄）。解決しない＝スナップショットは読まない（照合は宣言そのもの）。
_OTHER_PAIR = (sss.OANDA_JAPAN_MT5_LIVE, "USDJPY")

#: 期待する列（実装から導かず綴る＝列が 1 つ消えても気付ける）。
_VALUE_COLUMNS = ["open", "high", "low", "close", "volume", "up", "dn"]
_SPREAD_COLUMN = "spread"

_T0 = pd.Timestamp("2026-09-01 09:00:00")


def _frame(minutes: int, per_minute: int = 5) -> pd.DataFrame:
    """``minutes`` 分ぶんのティック（価格も気配幅も分ごと・ティックごとに動く）。

    定数列にしないのは、射影の補題が「どちらも同じ定数」で空振りしないためである。
    """
    rows = []
    for m in range(minutes):
        for i in range(per_minute):
            bid = 66000.0 + m * 2.0 - i * 0.3 + (i % 2) * 0.7
            rows.append((
                _T0 + pd.Timedelta(minutes=m, seconds=i * (60 // per_minute)),
                bid,
                bid + 7.0 + (m % 3) * 0.5 + i * 0.1,
            ))
    return pd.DataFrame(rows, columns=["timestamp", "bidPrice", "askPrice"])


def _register_pair(monkeypatch, tmp_path) -> None:
    """spread 付き ref と spread 無し ref を、同じ価格基準で台帳へ一時登録する。"""
    register_tick_ref(monkeypatch, tmp_path, _SPREAD_REF, SNAPSHOT_PAIR)
    register_tick_ref(monkeypatch, tmp_path, _PLAIN_REF, None)


def _column_match(left: pd.DataFrame, right: pd.DataFrame) -> "dict[str, int]":
    """列ごとに「値が一致した行数」を数える（数え方を報告できる形で残す）。"""
    return {c: int((left[c] == right[c]).sum()) for c in _VALUE_COLUMNS}


# =====================================================================
# L-1 射影の補題（畳みの口）
# =====================================================================
@pytest.mark.parametrize("minutes,per_minute", [(6, 5), (23, 3)])
def test_l1_dropping_the_spread_column_equals_folding_without_spread(
    tmp_path, monkeypatch, minutes, per_minute
):
    """L-1: spread 付きの畳みから spread 列を落とした結果が、spread 無しの畳みと**値まで**一致する。

    一致を見る範囲は index（分バーの date）・列名と列順・dtype・7 値列すべての値である。
    行数だけの突合にしないのは、bid/ask 列を作業表へ同乗させたことで並べ替えや groupby が
    動く退行が、行数では現れないためである（設計 D-1 の T-1＝値の一致は未実測）。
    """
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    ticks = _frame(minutes, per_minute)
    plan = tick_m1.series_plan((_SPREAD_REF, _PLAIN_REF), data_dir=tmp_path)
    alone = tick_m1.ticks_to_m1(ticks, price_basis=LEDGER_BASIS, point=None)

    # Act
    got = tick_m1.fold_ticks_for_series(ticks, plan=plan)

    # Assert: 列形（spread 付きは列の上位集合・spread 無しは射影）。
    with_spread = got[_SPREAD_REF]
    projected = got[_PLAIN_REF]
    assert list(with_spread.columns) == _VALUE_COLUMNS + [_SPREAD_COLUMN]
    assert list(projected.columns) == _VALUE_COLUMNS
    # 空振り防止: 実際に畳んでおり、値も気配幅も定数ではない。
    assert (len(projected), len(alone)) == (minutes, minutes)
    assert projected["close"].nunique() > 1
    assert with_spread[_SPREAD_COLUMN].nunique() > 1

    # Assert: 全列の値・dtype・index が一致（射影 vs 独立に畳んだ spread 無し）。
    assert _column_match(projected, alone) == {c: minutes for c in _VALUE_COLUMNS}
    pd.testing.assert_frame_equal(projected, alone, check_exact=True)
    # Assert: spread 付き側の値部分も同じ（列を足しただけ＝上位集合）。
    pd.testing.assert_frame_equal(
        with_spread.drop(columns=[_SPREAD_COLUMN]), alone, check_exact=True
    )


# =====================================================================
# L-2 射影の補題（素材化の口）
# =====================================================================
@pytest.mark.parametrize("minutes,per_minute", [(6, 5), (23, 3)])
def test_l2_the_day_materializer_projects_instead_of_recomputing(
    tmp_path, monkeypatch, minutes, per_minute
):
    """L-2: 素材化（外れ分除去・行選択の後）でも、射影と独立の素材化が値まで一致する。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    ticks = _frame(minutes, per_minute)
    plan = tick_m1.series_plan((_SPREAD_REF, _PLAIN_REF), data_dir=tmp_path)
    alone = tick_m1.materialize_m1_day(ticks, ref=_PLAIN_REF)

    # Act
    got = tick_m1.materialize_m1_day_for_series(ticks, plan=plan, after=None, until=None)

    # Assert
    projected = got[_PLAIN_REF]
    assert list(projected.columns) == _VALUE_COLUMNS
    assert list(got[_SPREAD_REF].columns) == _VALUE_COLUMNS + [_SPREAD_COLUMN]
    assert (len(projected), len(alone)) == (minutes, minutes)          # 空振り防止
    assert _column_match(projected, alone) == {c: minutes for c in _VALUE_COLUMNS}
    pd.testing.assert_frame_equal(projected, alone, check_exact=True)


def test_l2_row_selection_still_applies_to_both_series(tmp_path, monkeypatch):
    """L-2b: 行選択（after で既存最終分より後・until で形成中分の除外）は 2 系列に等しく効く。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    ticks = _frame(minutes=8, per_minute=4)

    # Act
    got = tick_m1.materialize_m1_day_for_series(
        ticks,
        plan=tick_m1.series_plan((_SPREAD_REF, _PLAIN_REF), data_dir=tmp_path),
        after=_T0 + pd.Timedelta(minutes=1),
        until=_T0 + pd.Timedelta(minutes=6),
    )

    # Assert: 残る分は 2..5 の 4 本（両系列で同じ index）。
    assert len(got[_PLAIN_REF]) == 4                                   # 空振り防止
    assert list(got[_PLAIN_REF].index) == list(got[_SPREAD_REF].index)
    assert got[_PLAIN_REF].index.min() == _T0 + pd.Timedelta(minutes=2)
    assert got[_PLAIN_REF].index.max() == _T0 + pd.Timedelta(minutes=5)


# =====================================================================
# CX-1 畳みは 1 回だけ（発行 − 使用 = 0・系列数で増えない）
# =====================================================================
def _spy_folds(monkeypatch) -> "list[int]":
    """唯一の畳み点 ``tick_m1._fold_ticks`` を包み、発行ごとに畳んだティック行数を記録する。

    数え方は ``marketdata/tests/test_mt5_rebuild_materialize.py`` と同じ（入口ではなく共通前段）。
    """
    folded: "list[int]" = []
    real = tick_m1._fold_ticks

    def spy(ticks, *args, **kwargs):
        folded.append(len(ticks))
        return real(ticks, *args, **kwargs)

    monkeypatch.setattr(tick_m1, "_fold_ticks", spy)
    return folded


@pytest.mark.parametrize("minutes", [4, 40], ids=["4min", "40min"])
@pytest.mark.parametrize(
    "refs", [(_SPREAD_REF,), (_SPREAD_REF, _PLAIN_REF)], ids=["1series", "2series"]
)
def test_cx1_folding_is_issued_once_regardless_of_the_number_of_series(
    tmp_path, monkeypatch, minutes, refs
):
    """CX-1: 発行（畳んだ行数）− 使用（入力の行数）= 0。系列数でも分数でも比例しない。

    素朴に系列ごとへ畳むと、2 系列で発行が入力の 2 倍になる（出力は正しいままなので
    状態検証では原理的に落ちない・ISSUE-450 と同型）。
    """
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    ticks = _frame(minutes, per_minute=5)
    plan = tick_m1.series_plan(refs, data_dir=tmp_path)
    folded = _spy_folds(monkeypatch)

    # Act
    got = tick_m1.fold_ticks_for_series(ticks, plan=plan)

    # Assert
    used = len(ticks)
    assert used > 0 and len(got[refs[0]]) == minutes                   # 空振り防止
    assert sum(folded) - used == 0, (
        f"系列 {len(refs)} 本・{minutes} 分: 畳み {folded} − 入力ティック {used} ≠ 0"
    )


def test_cx1_the_issued_fold_does_not_grow_with_the_number_of_series(tmp_path, monkeypatch):
    """CX-1b: 系列数 1 → 2 で発行が等しい（オーダーの表明・規模 2 点は分数でも取る）。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    issued: "dict[tuple[int, int], int]" = {}

    for minutes in (4, 40):
        for refs in ((_SPREAD_REF,), (_SPREAD_REF, _PLAIN_REF)):
            ticks = _frame(minutes, per_minute=5)
            plan = tick_m1.series_plan(refs, data_dir=tmp_path)
            folded = _spy_folds(monkeypatch)

            # Act
            out = tick_m1.fold_ticks_for_series(ticks, plan=plan)

            assert len(out) == len(refs)                               # 空振り防止
            issued[(minutes, len(refs))] = sum(folded)

    # Assert: 系列数を増やしても発行は増えない（分数ごとに突き合わせる）。
    for minutes in (4, 40):
        assert issued[(minutes, 1)] == issued[(minutes, 2)], (
            f"{minutes} 分で系列数 1 → 2 にしたら畳みが {issued[(minutes, 1)]} →"
            f" {issued[(minutes, 2)]} 行へ増えました（系列ごとに畳み直しています）。"
        )
    # 空振り防止: 分数を 10 倍にしたら発行は実際に増える（測っている量が動く）。
    assert issued[(4, 2)] < issued[(40, 2)]


def test_cx1_the_day_materializer_folds_once_for_both_series(tmp_path, monkeypatch):
    """CX-1c: 素材化の口でも発行 − 使用 = 0（2 系列で畳みは 1 回）。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    ticks = _frame(minutes=12, per_minute=4)
    plan = tick_m1.series_plan((_SPREAD_REF, _PLAIN_REF), data_dir=tmp_path)
    folded = _spy_folds(monkeypatch)

    # Act
    got = tick_m1.materialize_m1_day_for_series(ticks, plan=plan, after=None, until=None)

    # Assert
    assert len(got[_PLAIN_REF]) == 12                                  # 空振り防止
    assert sum(folded) - len(ticks) == 0, f"畳み {folded} − 入力ティック {len(ticks)} ≠ 0"


# =====================================================================
# F-1 案内の Fail-Stop
# =====================================================================
def test_f1_a_conflicting_price_basis_is_refused_by_name(tmp_path, monkeypatch):
    """F-1: 価格基準が食い違う ref の組は、名前付きの例外で止まる（黙って片方の基準で畳まない）。"""
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _SPREAD_REF, SNAPSHOT_PAIR)
    register_tick_ref(monkeypatch, tmp_path, _OTHER_BASIS_REF, None, basis="mid")

    # Act / Assert
    with pytest.raises(tick_m1.SeriesPlanConflict) as err:
        tick_m1.series_plan((_SPREAD_REF, _OTHER_BASIS_REF), data_dir=tmp_path)
    assert _SPREAD_REF in str(err.value) and _OTHER_BASIS_REF in str(err.value)


def test_f1_conflicting_spread_declarations_are_refused_by_name(tmp_path, monkeypatch):
    """F-1b: spread の宣言（どの組の point で数えるか）が食い違う組は止まる。

    畳みを 1 回にできるのは、spread を数える point が組で 1 つだからである。宣言が割れた
    まま通すと、片方の系列が別銘柄の point で数えた spread を持つ（値は形式上正しい）。
    """
    # Arrange
    register_tick_ref(monkeypatch, tmp_path, _SPREAD_REF, SNAPSHOT_PAIR)
    register_tick_ref(monkeypatch, tmp_path, _OTHER_SNAPSHOT_REF, _OTHER_PAIR)

    # Act / Assert
    with pytest.raises(tick_m1.SeriesPlanConflict) as err:
        tick_m1.series_plan((_SPREAD_REF, _OTHER_SNAPSHOT_REF), data_dir=tmp_path)
    assert _OTHER_SNAPSHOT_REF in str(err.value)


def test_f1_an_empty_set_of_series_is_refused(tmp_path):
    """F-1c: 系列が 0 件の案内は作れない（0 件を正常として受け取り何も書かない経路を作らない）。

    文面まで見るのは、0 件でも後段の「価格基準が 1 つに定まらない」判定に**巻き込まれて**同じ型で
    止まるためである（実測: 0 件の判定を外しても型だけの検定は緑のまま＝検出力 0）。止まった理由が
    「基準が食い違う: {}」と読める状態は、運用で原因を取り違える。
    """
    with pytest.raises(tick_m1.SeriesPlanConflict, match="系列が 1 つも無い"):
        tick_m1.series_plan((), data_dir=tmp_path)


def test_f1_a_repeated_ref_is_refused(tmp_path, monkeypatch):
    """F-1d: 同じ ref が 2 度現れる案内は作れない（案内の並びと出力の対応が 1 対 1 でなくなる）。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)

    # Act / Assert
    with pytest.raises(tick_m1.SeriesPlanConflict):
        tick_m1.series_plan((_SPREAD_REF, _PLAIN_REF, _SPREAD_REF), data_dir=tmp_path)


def test_f1_the_named_conflict_is_a_value_error(tmp_path, monkeypatch):
    """F-1e: 名前付きの型であること（握り手が型で区別できる）。"""
    assert issubclass(tick_m1.SeriesPlanConflict, ValueError)


# =====================================================================
# F-2 案内が受けた ref は 1 つも落ちない
# =====================================================================
@pytest.mark.parametrize("empty", [False, True], ids=["nonempty", "empty"])
def test_f2_every_ref_of_the_plan_appears_in_the_output(tmp_path, monkeypatch, empty):
    """F-2: 出力の鍵が案内の並びと 1 対 1（空入力でも列形は宣言どおり）。

    案内から要素を落とす変異（片方だけ返す）は、返った側だけを見る検定では素通りする。
    """
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    refs = (_SPREAD_REF, _PLAIN_REF)
    plan = tick_m1.series_plan(refs, data_dir=tmp_path)
    ticks = _frame(minutes=0 if empty else 5)

    # Act
    folded = tick_m1.fold_ticks_for_series(ticks, plan=plan)
    materialized = tick_m1.materialize_m1_day_for_series(ticks, plan=plan, after=None, until=None)

    # Assert
    assert tuple(plan.refs) == refs
    assert tuple(folded) == refs and tuple(materialized) == refs
    assert list(folded[_SPREAD_REF].columns) == _VALUE_COLUMNS + [_SPREAD_COLUMN]
    assert list(folded[_PLAIN_REF].columns) == _VALUE_COLUMNS
    assert (len(folded[_PLAIN_REF]) == 0) is empty                      # 空振り防止


# =====================================================================
# E-1 既存の口の等価（名前・シグネチャ・戻り値）
# =====================================================================
#: 既存の口の現行シグネチャ（段 2 の着手時点の実測値をそのまま綴る＝変わったら赤）。
_FROZEN_SIGNATURES = {
    "fold_ticks_for": (
        "(ticks: 'pd.DataFrame', *, ref: 'str', price_basis: \"'str | None'\" = None,"
        " data_dir: 'Any') -> 'pd.DataFrame'"
    ),
    "materialize_m1_day": (
        "(ticks: 'pd.DataFrame', *, ref: 'str', price_basis: \"'str | None'\" = None)"
        " -> 'pd.DataFrame'"
    ),
}


@pytest.mark.parametrize("name", sorted(_FROZEN_SIGNATURES))
def test_e1_the_existing_entries_keep_their_name_and_signature(name):
    """E-1: 既存の口はモジュール属性として在り、名前・引数・戻り値の型も変わらない（Guard・前後とも緑）。

    ``from ... import`` へ変えたり引数を足したりすると、既存の計算量検定の Test Spy
    （モジュール属性の差し替え）が空振りで緑になる。
    """
    fn = getattr(tick_m1, name)
    assert callable(fn)
    assert str(inspect.signature(fn)) == _FROZEN_SIGNATURES[name]


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
def test_e1_fold_ticks_for_equals_the_one_element_plan(tmp_path, monkeypatch, declared):
    """E-1b: ``fold_ticks_for`` の戻り値が、1 要素の案内を通した結果と一致する。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    ref = _SPREAD_REF if declared else _PLAIN_REF
    ticks = _frame(minutes=7, per_minute=4)

    # Act
    direct = tick_m1.fold_ticks_for(ticks, ref=ref, data_dir=tmp_path)
    via_plan = tick_m1.fold_ticks_for_series(
        ticks, plan=tick_m1.series_plan((ref,), data_dir=tmp_path)
    )[ref]

    # Assert
    assert len(direct) == 7                                            # 空振り防止
    assert (_SPREAD_COLUMN in direct.columns) is declared
    pd.testing.assert_frame_equal(direct, via_plan, check_exact=True)


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
def test_e1_materialize_m1_day_equals_the_one_element_plan(tmp_path, monkeypatch, declared):
    """E-1c: ``materialize_m1_day`` の戻り値が、1 要素の案内を通した結果と一致する。"""
    # Arrange
    _register_pair(monkeypatch, tmp_path)
    ref = _SPREAD_REF if declared else _PLAIN_REF
    ticks = _frame(minutes=7, per_minute=4)

    # Act
    direct = tick_m1.materialize_m1_day(ticks, ref=ref)
    via_plan = tick_m1.materialize_m1_day_for_series(
        ticks, plan=tick_m1.series_plan((ref,), data_dir=tmp_path), after=None, until=None
    )[ref]

    # Assert
    assert len(direct) == 7                                            # 空振り防止
    assert (_SPREAD_COLUMN in direct.columns) is declared
    pd.testing.assert_frame_equal(direct, via_plan, check_exact=True)
