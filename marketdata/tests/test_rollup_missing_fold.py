"""欠損オペランドの畳みと、チャンク幅に依存しない出力（ISSUE-511 段階 3・工程 5 是正 3）。

固定するもの:
    R-M1 `rb.stream_build` の出力が ``chunk_rows`` に依存しない（欠損入りの素材で）。
    R-M2 台帳の畳み（`csv_schema.combine_for`）が欠損オペランドを飛ばす（順序に依らない）。
    R-M3 `rb.merge_same_period` が、片側が欠損でも可換に畳む。
    CX-F 欠損が混ざっても、combine の発行 − 出力バーの列数 = 0（列数 2 点）。

なぜ在るか（是正前の実測・pandas 3.0.3）:
    `csv_schema.combine_for` が返すスカラ二項面は Python の ``min`` / ``max`` をそのまま
    使っており、欠損を飛ばさない。実測: ``min(欠損, 70)`` は欠損・``min(70, 欠損)`` は 70
    ＝**可換でない**。`rb.merge_same_period` はチャンク跨ぎの carry-over でしか呼ばれず、
    引数の順序は「前のチャンクの partial, 次のチャンクの partial」に固定されているため、
    **どこでチャンクを切ったか**が出力を変える。

    実測（本ファイルと同じ素材＝180 分・60〜99 分の spread が空欄・1 時間足）:
        chunk_rows=90 / 100      -> ['70', '',   '70']
        chunk_rows=120 / 1000000 -> ['70', '70', '70']
        全件 resample_ohlc       -> [70.0, 70.0, 70.0]
    `marketdata.rollup` は冒頭で「``stream_build`` の結果は ``resample_ohlc(全件, rule)`` と
    完全一致する」と宣言しており、上の食い違いはその宣言に対する反例である。出力は
    「それらしい」ままなので、値を見る検査では原理的に落ちない。

    是正前（段階 2 の前）はここで ``int(NaN)`` が ``ValueError`` を出して止まっていた。
    欠損を通す規約を入れたことで初めてこの沈黙が開いた＝本段が開けた口である。

射程（誇大にしないための限定）:
    合成データのみを使い、``data/marketdata/**`` を 1 バイトも読まない。実ロールアップ
    32 ファイル（8 TF × 4 系列）を数え直した結果は、データ行 5,153,620・**値セル
    32,892,780**（date 列を除く）・空欄 0 件である（実測 2026-09-17・読み取りのみ）。
    かつてここに書かれていた 38,045,890 は**誤りではなく別の数え方**（date 列を含めた
    全セル。同時点の実測は 38,046,400 で、差 5,153,620 はデータ行数にちょうど等しい）。
    数え方を書かずに係数だけを残したのが欠陥だったため、定義を明示する。
    なお ``tools/live_tick_watch.py`` が追記し続けるため、この値は**ある時点のスナップ
    ショット**であり単調に増える（固定値として引用してはならない）。空欄 0 件という
    事実だけが、追記では変わらない。欠損が通常経路で生まれるとは主張していない。ここで
    固定するのは**防御**としての規約である。

    欠損を飛ばす規約が pandas の列別集約と一致することは min・max・first・last について
    実測した（いずれも欠損を飛ばす）。sum は**両方が欠損**のときだけ pandas が 0.0 を
    返すのに対し本規約は欠損を返す。この 1 点は一致しない（未検証の一般化を書かない
    ための明示であり、畳みの可換性・結合性には影響しない）。
    さらにこの不一致は `rb.stream_build` からは**到達しない**（実測 2026-09-17）:
    合算列が全欠損の期間について pandas の ``resample().agg("sum")`` が ``0.0`` を返すため、
    `rb.merge_same_period` へ渡る bar の合算列は欠損になりえない（`rb._resample_chunk` を
    通した実測: 60 分すべて欠損の 1 時間足で volume 列は ``0.0``）。観測できるのは
    本二項面（`csv_schema.combine_for`）を直接呼んだときだけである。
"""
from __future__ import annotations

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

#: 素材: 180 分。60〜99 分の spread だけが空欄（＝2 本目の 1 時間足の前半だけが欠損）。
#: この形でなければチャンク境界が「全欠損の partial」と「値を持つ partial」の間に落ちない。
_MINUTES, _BLANK_FROM, _BLANK_TO = 180, 60, 100

#: 全件 resample_ohlc が出す答え（実測 [70.0, 70.0, 70.0]）を CSV の表記で書いたもの。
_EXPECTED_SPREADS = ["70", "70", "70"]

#: 測定するチャンク幅。90 / 100 は 2 本目の期間の内側で切れ、120 / 1000000 は切れない。
_CHUNK_WIDTHS = (90, 100, 120, 1_000_000)


def _m1_with_a_blank_stretch(path: Path) -> None:
    """spread の一部だけが空欄の M1 CSV を書く（列が増える前に書かれた旧行の模擬）。"""
    df = _synthetic_m1("2020-01-06 00:00:00", _MINUTES, with_spread=True)
    cells: "list[Any]" = [
        "" if _BLANK_FROM <= i < _BLANK_TO else v for i, v in enumerate(df[_SPREAD])
    ]
    _write_m1_csv(path, df.assign(**{_SPREAD: pd.Series(cells, index=df.index, dtype=object)}))


def _written_spreads(path: Path) -> "list[str]":
    """書かれたロールアップ CSV の spread 列を**文字列のまま**返す。"""
    position = (rb._header_of(path) or []).index(_SPREAD)
    return [row[position] for row in _rollup_rows(path)]


# =====================================================================
# R-M1: 出力がチャンク幅に依存しない
# =====================================================================

def test_the_full_resample_is_the_oracle_for_this_material() -> None:
    """素材に対する正解（`md_resample.resample_ohlc` の全件集計）を先に固定する。

    `marketdata.rollup` の宣言が「``stream_build`` の結果は全件 resample と完全一致」で
    ある以上、期待値は `rb.stream_build` の出力からではなくこちらから引く。自分の
    実装の出力を期待値にすると、ずれたときに一緒にずれて恒真になる。
    """
    df = _synthetic_m1("2020-01-06 00:00:00", _MINUTES, with_spread=True)
    df.loc[df.index[_BLANK_FROM:_BLANK_TO], _SPREAD] = np.nan

    folded = md_resample.resample_ohlc(df, "1h")

    assert [str(int(v)) for v in folded[_SPREAD]] == _EXPECTED_SPREADS
    # 素材の健全性（欠損が実在し、かつ値を持つ行も実在する）。
    assert bool(df[_SPREAD].isna().any())
    assert bool(df[_SPREAD].notna().any())


def test_the_stream_build_output_does_not_depend_on_the_chunk_width(tmp_path: Path) -> None:
    """R-M1: どのチャンク幅で読んでも、欠損入り素材の出力は全件 resample と一致する。

    チャンク幅は「1 分足を二度と全ロードしない」ためのメモリの都合であって、値の規則では
    ない。幅で答えが変わるなら、carry-over の畳みが結合的でない（＝`rb.merge_same_period`
    の前提が崩れている）。是正前の実測では 90 / 100 と 120 / 1000000 で答えが割れた。
    """
    m1 = tmp_path / "m1.csv"
    _m1_with_a_blank_stretch(m1)

    written = {}
    for chunk_rows in _CHUNK_WIDTHS:
        out = tmp_path / f"out_{chunk_rows}"
        rb.stream_build(m1, ["1h"], out, "ref", chunk_rows=chunk_rows)
        written[chunk_rows] = _written_spreads(out / "ref_1h.csv")

    assert written == {chunk_rows: _EXPECTED_SPREADS for chunk_rows in _CHUNK_WIDTHS}


# =====================================================================
# R-M2 / R-M3: 台帳の畳みが欠損オペランドを飛ばす
# =====================================================================

@pytest.mark.parametrize("missing", [float("nan"), np.nan, None])
def test_the_ledger_fold_skips_a_missing_operand_in_either_order(missing: Any) -> None:
    """R-M2: 欠損は飛ばして、もう一方の値を採る（左右どちらが欠損でも同じ）。

    是正前は Python の ``min`` をそのまま使っており、``min(欠損, 70)`` が欠損・
    ``min(70, 欠損)`` が 70 になっていた（実測）。畳みが可換でなければ、同じ素材でも
    分割の仕方で答えが変わる。
    """
    fold = csv_schema.combine_for(_SPREAD)

    assert fold(missing, 70) == 70
    assert fold(70, missing) == 70


def test_the_ledger_fold_keeps_the_missing_value_when_both_operands_are_missing() -> None:
    """R-M2（境界）: 両方が欠損なら欠損のまま（値を捏造しない）。"""
    fold = csv_schema.combine_for(_SPREAD)

    folded = fold(np.nan, np.nan)

    assert folded != folded  # 非数は自分自身と等しくない（欠損のまま残っている）


def test_the_ledger_fold_is_unchanged_when_no_operand_is_missing() -> None:
    """R-M2（副作用なし）: 欠損が無いときの畳み方は 1 つも変わらない。"""
    assert csv_schema.combine_for(_SPREAD)(75, 71) == 71
    assert csv_schema.combine_for("high")(3.0, 9.0) == 9.0
    assert csv_schema.combine_for("open")(1.0, 2.0) == 1.0
    assert csv_schema.combine_for("close")(1.0, 2.0) == 2.0
    assert csv_schema.combine_for("volume")(10.0, 20.0) == 30.0


def test_merge_same_period_is_commutative_when_one_bar_has_a_missing_cell() -> None:
    """R-M3: 片側の bar が欠損セルを持っていても、畳む順序で答えが変わらない。

    `rb.merge_same_period` は carry-order でしか呼ばれないため、順序依存は「どこで
    チャンクを切ったか」として出力に現れる。順序に依らないことを直接固定する。
    """
    without = {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 10.0,
               _SPREAD: np.nan}
    with_value = {"open": 2.0, "high": 4.0, "low": 1.5, "close": 3.0, "volume": 20.0,
                  _SPREAD: 70}

    forward = rb.merge_same_period(without, with_value)
    backward = rb.merge_same_period(with_value, without)

    assert forward[_SPREAD] == 70
    assert forward[_SPREAD] == backward[_SPREAD]


def test_merge_same_period_is_associative_when_a_bar_has_a_missing_cell() -> None:
    """R-M3（結合性）: 欠損が混ざっても ``merge(merge(a,b),c) == merge(a,merge(b,c))``。

    結合性はチャンク跨ぎ carry-over の正しさ（D-1）の根拠であり、チャンク幅に依らない
    出力の根拠でもある。
    """
    a = {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 10.0,
         _SPREAD: np.nan}
    b = {"open": 2.0, "high": 9.0, "low": 1.5, "close": 3.0, "volume": 20.0, _SPREAD: 71}
    c = {"open": 3.0, "high": 4.0, "low": 0.1, "close": 5.0, "volume": 30.0, _SPREAD: 73}

    left = rb.merge_same_period(rb.merge_same_period(a, b), c)
    right = rb.merge_same_period(a, rb.merge_same_period(b, c))

    assert left == right
    assert left[_SPREAD] == 71


# =====================================================================
# CX-F: 欠損を飛ばすために余分な畳みを発行しない
# =====================================================================

@pytest.mark.parametrize("bar_columns", [
    ["open", "high", "low", "close", "volume"],
    ["open", "high", "low", "close", "volume", "up", "dn", _SPREAD],
])
def test_the_scalar_combine_is_issued_once_per_output_column_with_a_missing_cell(
        bar_columns: "list[str]", monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-F: combine の発行 − 出力バーの列数 = 0（欠損が混ざっても・列数 2 点）。

    欠損を飛ばす規約は、判定のために値をもう一度畳み直す実装でも同じ出力になる。
    出力が正しいまま発行だけが増える形なので、値の検査では原理的に落ちない。
    """
    prev_bar: "dict[str, Any]" = {c: 2.0 for c in bar_columns}
    prev_bar[bar_columns[-1]] = np.nan
    new_bar: "dict[str, Any]" = {c: 3.0 for c in bar_columns}
    issued: "list[Any]" = []
    real = csv_schema.combine_for

    def counting(column: Any) -> Any:
        fold = real(column)

        def counted(prev: Any, new: Any) -> Any:
            issued.append(column)
            return fold(prev, new)

        return counted

    monkeypatch.setattr(csv_schema, "combine_for", counting)

    merged = rb.merge_same_period(prev_bar, new_bar)

    assert len(issued) - len(merged) == 0
    assert len(merged) == len(bar_columns)
