"""欠損セルの型付けを**値単位**で決める（ISSUE-511 段階 3・段階 2）。

固定するもの:
    R-4  欠損を含む全件 rewrite で、値のある行は ``70``（``70.0`` ではない）・欠損行は空欄。
    R-5  欠損を含む bar が例外にならない（``stream_build`` が最初の旧 period で落ちない）。
    R-5b csv.writer の面でも欠損は空欄（非数を文字列として書かない）。
    R-6  欠損**以外**の理由で cast が失敗したら :class:`marketdata.rollup.RollupCellCastError`
         が上がり、**列名と period の両方**がメッセージに含まれる（適用点 3 箇所それぞれ）。

計算量（発行 − 使用 = 0・2 点以上・回数は焼き込まない）:
    CX-C 継ぎ目 ``csv_schema.cast_for`` とその返す関数。cast 発行 − 書いたセル数 = 0
         （＝空欄へ cast を発行しない）。行数 2 点で固定する。

なぜ在るか（是正前の実測・pandas 3.0.3）:
    ``_write_rollup_df`` は ``notna().all()`` の**列単位**分岐で型を決めており、欠損を 1 つでも
    含む列は cast を通らなかった。そのため既に ``70`` と書かれていた行が、全件 rewrite の
    たびに ``70.0`` へ戻る（実測: 値のある 3 行がすべて ``70.0``）。「欠損行を空欄のまま残す」
    ことと「値のある行を整数で書く」ことは両立する。両立しないのは、型を**列単位**で
    決めたときだけである。
    ``stream_build`` 側は逆に、同じ素材で ``ValueError: cannot convert float NaN to integer``
    を出して最初の旧 period で止まっていた（実測・``_bar_to_dict``）。

射程（誇大にしないための限定）:
    本検定はすべて合成データで組み立て、``data/marketdata/**`` を 1 バイトも読まない。
    実データに欠損が現に存在するという主張はしていない。ここで固定するのは**防御**として
    の規約であって、欠損が通常経路で生まれるという前提ではない。
"""
from __future__ import annotations

import csv as _csv
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from marketdata import csv_schema
from marketdata import resample as md_resample
from marketdata import rollup as rb

# 合成素材の組み立ては既存検定と同一の唯一源から借りる（手書き複製を作らない）。
from test_rollup_spread_aggregation import (  # type: ignore[import-not-found]
    _rollup_rows,
    _synthetic_m1,
    _write_m1_csv,
)

_SPREAD = csv_schema.SPREAD_COLUMN


# --------------------------------------------------------------------------- #
# 合成素材（欠損を含む）
# --------------------------------------------------------------------------- #
def _hourly_frame_with_a_missing_spread(minutes: int) -> pd.DataFrame:
    """1 時間足へ畳んだ frame。先頭 1 本だけ spread が欠損している（他は値を持つ）。

    欠損は「spread 列が増える前に書かれた旧行」を模したものであり、実データの観測ではない。
    """
    resampled = md_resample.resample_ohlc(
        _synthetic_m1("2020-01-06 00:00:00", minutes, with_spread=True), "1h"
    )
    resampled.loc[resampled.index[0], _SPREAD] = np.nan
    return resampled


def _written_column(path: Path, column: str) -> "list[str]":
    """書かれた CSV の当該列を**文字列のまま**返す（表記そのものを見る）。"""
    position = (rb._header_of(path) or []).index(column)
    return [row[position] for row in _rollup_rows(path)]


def _m1_csv_whose_first_hour_has_no_spread(path: Path, minutes: int) -> None:
    """先頭 1 時間の spread セルだけが空欄の M1 CSV を書く（旧行の模擬）。"""
    df = _synthetic_m1("2020-01-06 00:00:00", minutes, with_spread=True)
    cells: "list[Any]" = ["" if i < 60 else v for i, v in enumerate(df[_SPREAD])]
    _write_m1_csv(path, df.assign(**{_SPREAD: pd.Series(cells, index=df.index, dtype=object)}))


# =====================================================================
# R-4: 欠損を含む全件 rewrite（値は整数・欠損は空欄）
# =====================================================================

def test_the_full_rewrite_writes_the_values_as_integers_and_leaves_the_gaps_blank(
        tmp_path: Path) -> None:
    """R-4: 欠損を含む列でも、値のあるセルは ``70``・欠損セルは空欄で書かれる。

    是正前はここが ``70.0`` だった（列単位分岐が cast を丸ごと飛ばすため）。欠損セルの
    空欄は是正前後で変わらない（旧行の値を捏造しない）。
    """
    frame = _hourly_frame_with_a_missing_spread(240)
    # 前提の明示（この 2 つが崩れると本検定は欠損混在の経路を見ていない）。
    assert bool(frame[_SPREAD].isna().any())
    assert bool(frame[_SPREAD].notna().any())

    rb._write_rollup_df(tmp_path, "1h", frame, "ref")

    assert _written_column(tmp_path / "ref_1h.csv", _SPREAD) == ["", "70", "70", "70"]


def test_the_full_rewrite_does_not_change_the_other_columns_when_a_gap_exists(
        tmp_path: Path) -> None:
    """R-4（副作用なし）: 欠損のある列の隣で、価格列の表記は従来どおり float。"""
    rb._write_rollup_df(tmp_path, "1h", _hourly_frame_with_a_missing_spread(240), "ref")

    assert _written_column(tmp_path / "ref_1h.csv", "open") == [
        "100.0", "160.0", "220.0", "280.0",
    ]


# =====================================================================
# R-5: 欠損を含む bar が例外にならない
# =====================================================================

def test_stream_build_does_not_die_on_the_first_period_without_a_spread(
        tmp_path: Path) -> None:
    """R-5: 旧行（spread 空欄）を含む素材でも全件再構築が完走する。

    是正前は ``_bar_to_dict`` の ``int(NaN)`` が ``ValueError: cannot convert float NaN to
    integer`` を出し、最初の旧 period で止まった（実測）。止まると ``rb.heal_tail_gaps`` と
    MT5 日次再構築が機能停止する。
    """
    m1 = tmp_path / "m1.csv"
    _m1_csv_whose_first_hour_has_no_spread(m1, 180)

    rb.stream_build(m1, ["1h"], tmp_path / "out", "ref", chunk_rows=100)

    assert _written_column(tmp_path / "out" / "ref_1h.csv", _SPREAD) == ["", "70", "70"]


def test_the_bar_dict_passes_a_missing_value_through_without_casting_it() -> None:
    """R-5（bar 辞書の面）: 欠損は cast せず欠損のまま通す（carry-over が畳める形で残る）。"""
    row = pd.Series(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0,
         _SPREAD: np.nan},
        name=pd.Timestamp("2020-01-06 00:00:00"),
    )

    bar = rb._bar_to_dict(row)

    assert pd.isna(bar[_SPREAD])


def test_the_csv_row_writes_a_missing_value_as_a_blank_cell() -> None:
    """R-5b: csv.writer の面でも欠損は空欄。

    実測: csv.writer は float の非数を 3 文字の文字列として書く（空文字列と None は空欄）。
    欠損をそのまま渡すと、同じ欠損が経路によって空欄とその文字列の 2 通りに書き分かれる
    ＝本段で消そうとしている表記混在が別の形で残る。
    """
    bar = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0,
           _SPREAD: np.nan}

    row = rb._bar_to_csv_row(pd.Timestamp("2020-01-06 00:00:00"), bar)

    assert row[-1] == ""


# =====================================================================
# R-6: 欠損**以外**の cast 失敗は列名と period を添えた専用例外
# =====================================================================

#: 欠損ではないが cast できない値（素材破損の模擬）。
#:
#: 全角数字 ``"７０"`` は使えない: ``int()`` は Unicode の十進数字を**受理**するため
#: ``int("７０") == 70`` になり、破損の模擬にならない（実測。この取り違えで R-6 が
#: 一度空振りした）。前提は下の検定が機械的に押さえる。
_UNCASTABLE = "abc"
_PERIOD = pd.Timestamp("2020-01-06 01:00:00")


def test_the_uncastable_value_is_really_uncastable() -> None:
    """R-6（測定の前提）: 選んだ値が「欠損ではない」かつ「台帳の型へ戻せない」こと。

    前提を検定に持たせないと、値の選び方ひとつで R-6 の 3 件が**通ったまま空振り**する。
    """
    assert not pd.isna(_UNCASTABLE)
    with pytest.raises((TypeError, ValueError)):
        csv_schema.cast_for(_SPREAD)(_UNCASTABLE)


def test_the_dedicated_error_is_a_value_error() -> None:
    """R-6: 専用例外は ``ValueError`` の下位型（既存の捕捉面を狭めない）。"""
    assert issubclass(rb.RollupCellCastError, ValueError)


def test_the_bar_dict_reports_the_column_and_the_period_when_a_cast_fails() -> None:
    """R-6（適用点 1 ``_bar_to_dict``）: 列名と period の両方がメッセージに出る。"""
    row = pd.Series(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0,
         _SPREAD: _UNCASTABLE},
        name=_PERIOD,
    )

    with pytest.raises(rb.RollupCellCastError) as excinfo:
        rb._bar_to_dict(row)

    assert _SPREAD in str(excinfo.value)
    assert str(_PERIOD) in str(excinfo.value)


def test_the_csv_row_reports_the_column_and_the_period_when_a_cast_fails() -> None:
    """R-6（適用点 2 ``_bar_to_csv_row``）: 列名と period の両方がメッセージに出る。"""
    bar = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0,
           _SPREAD: _UNCASTABLE}

    with pytest.raises(rb.RollupCellCastError) as excinfo:
        rb._bar_to_csv_row(_PERIOD, bar)

    assert _SPREAD in str(excinfo.value)
    assert str(_PERIOD) in str(excinfo.value)


def test_the_full_rewrite_reports_the_column_and_the_period_when_a_cast_fails(
        tmp_path: Path) -> None:
    """R-6（適用点 3 ``_write_rollup_df``）: 列名と period の両方がメッセージに出る。

    列単位分岐では「cast を通らない列」は失敗すらせず素通りしていた（破損値がそのまま
    CSV へ出る）。値単位で決めると、欠損でない破損はここで止まる。
    """
    frame = _hourly_frame_with_a_missing_spread(240)
    frame[_SPREAD] = frame[_SPREAD].astype(object)
    frame.loc[_PERIOD, _SPREAD] = _UNCASTABLE

    with pytest.raises(rb.RollupCellCastError) as excinfo:
        rb._write_rollup_df(tmp_path, "1h", frame, "ref")

    assert _SPREAD in str(excinfo.value)
    assert str(_PERIOD) in str(excinfo.value)


def test_a_missing_value_is_not_reported_as_a_cast_failure() -> None:
    """R-6（境界）: 欠損は「失敗」ではない（専用例外は欠損以外の理由に限る）。

    同じ入口（:func:`marketdata.rollup._cast_cell`）が、欠損では例外を上げずに欠損を返し、
    欠損以外の cast 失敗でだけ専用例外を上げる。境界の両側を同じ口で見る。
    """
    cell = rb._cast_cell(_SPREAD, np.nan, period=_PERIOD)

    assert pd.isna(cell)


# =====================================================================
# CX-C: cast 発行 − 書いたセル数 = 0（空欄へ cast を発行しない・行数 2 点）
# =====================================================================

def _spy_on_casts(monkeypatch: pytest.MonkeyPatch) -> "list[str]":
    """継ぎ目 ``csv_schema.cast_for`` を包み、**返した関数が呼ばれた回数**を記録する。

    数えるのは規則の引き回数ではなく **cast の発行**（値 1 つを実際に変換した回数）である。
    """
    real = csv_schema.cast_for
    issued: "list[str]" = []

    def spying(column: Any) -> "Any":
        fn = real(column)

        def counted(value: Any) -> Any:
            issued.append(str(column))
            return fn(value)

        return counted

    monkeypatch.setattr(csv_schema, "cast_for", spying)
    return issued


def _written_cell_counts(path: Path) -> "tuple[int, int]":
    """書かれた CSV の値セル（date 列を除く）の（非空欄数, 空欄数）。"""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(_csv.reader(fh))[1:]
    cells = [c for row in rows for c in row[1:]]
    return sum(1 for c in cells if c != ""), sum(1 for c in cells if c == "")


@pytest.mark.parametrize("minutes", [240, 2400])
def test_the_full_rewrite_issues_no_cast_for_a_blank_cell(
        tmp_path: Path, minutes: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-C: cast 発行 − 書いた非空欄セル数 = 0（行数 2 点で成り立つ）。

    固定しているのは**無駄の不在**（空欄へ cast を発行しない）であって回数そのものでは
    ない。行数を変えた 2 点で同じ等式が成り立つことが、発行数が出力量だけで決まること
    （オーダーの表明）の表明である。
    """
    frame = _hourly_frame_with_a_missing_spread(minutes)
    issued = _spy_on_casts(monkeypatch)

    rb._write_rollup_df(tmp_path, "1h", frame, "ref")

    written, blank = _written_cell_counts(tmp_path / "ref_1h.csv")
    assert len(issued) - written == 0
    # 測定が空振りしていない（空欄が実在し、かつ書いたセルがある）。
    assert blank > 0
    assert written > 0


# =====================================================================
# CX-H: 規則の引き回数 − 型付けした列数 = 0（行が増えても引き直さない・行数 2 点）
#
# CX-C とは**別の性質**を見る。CX-C が数えるのは「cast の発行」（値 1 つを実際に変換した
# 回数）であり、値ごとに 1 回発行されるのが正しい。CX-H が数えるのは「どの型で書くかを
# 台帳へ問い合わせた回数」であり、これは列ごとに 1 回で足りる。値ごとに問い直しても
# **出力は 1 バイトも変わらない**ため、状態検証でも CX-C でも原理的に落ちない。
#
# 固定するのは無駄の不在（＝引き直しの不在）であって回数そのものではない。
# =====================================================================

def _spy_on_rule_lookups(
        monkeypatch: pytest.MonkeyPatch) -> "tuple[list[str], Any]":
    """継ぎ目 ``csv_schema.cast_for`` を包み、**規則を引いた回数**を記録する。

    返り値は（記録簿, 包む前の実体）。呼び手が測定区間の終わりで実体へ戻すため、
    同じ検定の中で規模を変えて 2 回測っても包みが二重に積み上がらない。
    """
    real = csv_schema.cast_for
    asked: "list[str]" = []

    def spying(column: Any) -> "Any":
        asked.append(str(column))
        return real(column)

    monkeypatch.setattr(csv_schema, "cast_for", spying)
    return asked, real


def _lookups_in_one_full_rewrite(
        tmp_path: Path, minutes: int,
        monkeypatch: pytest.MonkeyPatch) -> "dict[str, int]":
    """1 回の全件 rewrite について（規則の引き回数・型付けした列数・書いた行数）。"""
    frame = _hourly_frame_with_a_missing_spread(minutes)
    typed = [c for c in csv_schema.header_columns() if c in frame.columns]
    out_dir = tmp_path / str(minutes)

    asked, real = _spy_on_rule_lookups(monkeypatch)
    rb._write_rollup_df(out_dir, "1h", frame, "ref")
    monkeypatch.setattr(csv_schema, "cast_for", real)

    return {"asked": len(asked), "columns": len(typed),
            "rows": len(_rollup_rows(out_dir / "ref_1h.csv"))}


def test_the_full_rewrite_asks_the_type_rule_once_per_typed_column(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-H: ``cast_for`` の引き回数 − 型付けした列数 = 0（行数 2 点で不変）。

    行ごと・値ごとに規則を引き直す実装は、出力が正しいままここだけが落ちる。
    ``cast_for`` が返す ``_of()`` は列名を毎回 ``str().lower()`` して台帳を引くため、
    値ごとに引き直すと行数に比例した仕事が増える。増えているのは**出力に使わない
    引き直し**であって cast の発行ではない（cast の発行は CX-C が別に固定する）。
    """
    short = _lookups_in_one_full_rewrite(tmp_path, 240, monkeypatch)
    long = _lookups_in_one_full_rewrite(tmp_path, 2400, monkeypatch)

    assert short["asked"] - short["columns"] == 0
    assert long["asked"] - long["columns"] == 0
    # 行が 10 倍でも引き回数は増えない（オーダーの表明）。
    assert long["asked"] - short["asked"] == 0
    # 測定が空振りしていない（行が実際に増えており、型付けした列がある）。
    assert long["rows"] > short["rows"]
    assert short["columns"] > 0


# =====================================================================
# R-6b / R-6c: 包みの外を素通りしていた 2 つの失敗
#
# 段階 2 の要求は「**欠損以外**の理由で cast が失敗したら列名と period を添えた
# 専用例外」だが、現状は 2 つの経路が包みの外を通る（工程 4 の実測）。
#   R-6b 無限大は ``OverflowError``（``ValueError`` の下位型ではない）。
#   R-6c 非スカラは ``pd.isna`` 自身が素の ``ValueError`` を出す（包みへ到達する前）。
# どちらも列名も period も付かないため、どのファイルのどの足で何が壊れたか分からない。
# =====================================================================

#: 欠損ではないが cast できない値（無限大）。
_INFINITY = float("inf")

#: 欠損判定そのものを壊す値（要素 2 つ以上の配列様）。実測: ``pd.isna`` が要素ごとの
#: 配列を返し、``if`` の ``bool()`` が素の ``ValueError`` を出す。``tuple`` / ``dict`` /
#: ``set`` は ``pd.isna`` が ``False`` を返すため**この穴には当たらない**（既に包まれる）。
_NON_SCALARS = ([1, 2], np.array([1, 2]))


def test_an_infinite_value_fails_outside_the_wrapped_exception_types() -> None:
    """R-6b（測定の前提）: 無限大は欠損ではなく、cast は ``ValueError`` の下位型**でない**
    失敗を出す（実測 pandas 3.0.3 / CPython 3.13.5: ``OverflowError``）。

    前提を検定に持たせないと、包む型を広げた後で R-6b が**通ったまま空振り**する。
    """
    assert not pd.isna(_INFINITY)
    with pytest.raises(OverflowError):
        csv_schema.cast_for(_SPREAD)(_INFINITY)


@pytest.mark.parametrize("value", _NON_SCALARS)
def test_a_non_scalar_value_breaks_the_missing_check_itself(value: Any) -> None:
    """R-6c（測定の前提）: 非スカラは**欠損判定そのもの**を壊す（cast まで到達しない）。"""
    with pytest.raises(ValueError):
        bool(pd.isna(value))


def test_an_infinite_value_is_reported_with_the_column_and_the_period() -> None:
    """R-6b: 無限大の cast 失敗が列名と period を添えた専用例外になる。"""
    with pytest.raises(rb.RollupCellCastError) as excinfo:
        rb._cast_cell(_SPREAD, _INFINITY, period=_PERIOD)

    assert _SPREAD in str(excinfo.value)
    assert str(_PERIOD) in str(excinfo.value)


@pytest.mark.parametrize("value", _NON_SCALARS)
def test_a_non_scalar_value_is_reported_with_the_column_and_the_period(
        value: Any) -> None:
    """R-6c: 欠損判定を壊す値も列名と period を添えた専用例外になる。"""
    with pytest.raises(rb.RollupCellCastError) as excinfo:
        rb._cast_cell(_SPREAD, value, period=_PERIOD)

    assert _SPREAD in str(excinfo.value)
    assert str(_PERIOD) in str(excinfo.value)


def test_the_full_rewrite_reports_an_infinite_value_with_the_column_and_the_period(
        tmp_path: Path) -> None:
    """R-6b（適用点 3 ``_write_rollup_df``）: 実際の書き出し経路でも包まれる。

    入口（:func:`marketdata.rollup._cast_cell`）だけを見た検定は、適用点がその入口を
    通っていることを示さない。書き出し経路で同じ失敗が起きることを別に見る。
    """
    frame = _hourly_frame_with_a_missing_spread(240)
    frame[_SPREAD] = frame[_SPREAD].astype(object)
    frame.loc[_PERIOD, _SPREAD] = _INFINITY

    with pytest.raises(rb.RollupCellCastError) as excinfo:
        rb._write_rollup_df(tmp_path, "1h", frame, "ref")

    assert _SPREAD in str(excinfo.value)
    assert str(_PERIOD) in str(excinfo.value)
