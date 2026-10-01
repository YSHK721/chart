"""値列台帳（ISSUE-511 前提 (b) 段階 3b）の検定。

固定するもの:
    S-1 上位足の spread は最小値で畳む（``agg`` が ``"min"``）。
    S-2 既知の値列が「暗黙の last」へ落ちていない（台帳に行がある）。
    S-3 **2 面の一致**: 列別集約（pandas の ``groupby.agg``）と、同じ縮約のスカラ二項面
        （``combine``）を任意の分割で畳んだ結果が一致する。チャンク跨ぎ carry-over の
        正しさは後者に依っており、両者が食い違えば分割の仕方で値が変わる。
    S-4 台帳からの導出値（``VALUE_COLUMNS`` / ``SUM_COLUMNS`` / ``HEADER``）が
        現在と同一の内容・順序である（既存データの書式不変）。

期待値は台帳から導かず**リテラルで書き下す**。台帳から導けば、台帳が壊れたとき期待値も
一緒に壊れて恒真になる（C3 同型）。
"""
from __future__ import annotations

import functools

import pandas as pd
import pytest

from marketdata import csv_schema


#: 既知の値列（台帳とは独立に書き下した一覧）。vol はヘッダへ出ないが集約規則は持つ。
KNOWN_VALUE_COLUMNS = ["open", "high", "low", "close", "volume", "up", "dn", "spread", "vol"]

#: 各既知列の列別集約（書き下し）。
EXPECTED_AGG = {
    "open": "first", "high": "max", "low": "min", "close": "last",
    "volume": "sum", "up": "sum", "dn": "sum", "spread": "min", "vol": "sum",
}


# =====================================================================
# S-1 / S-2: 畳み方の宣言
# =====================================================================

def test_the_higher_timeframe_spread_is_folded_with_min() -> None:
    """上位足の spread は、その足に含まれる M1 spread の最小値（MT5 オラクルの規則）。"""
    assert csv_schema.agg_for("spread") == "min"


@pytest.mark.parametrize("column", KNOWN_VALUE_COLUMNS)
def test_every_known_value_column_declares_its_aggregation(column: str) -> None:
    """既知の各値列が宣言どおりの列別集約を返す。"""
    assert csv_schema.agg_for(column) == EXPECTED_AGG[column]


def test_no_known_value_column_falls_through_to_the_implicit_last() -> None:
    """既知の値列が台帳に無いまま「暗黙の last」で畳まれていない（0 件）。

    close の ``"last"`` は宣言された last であって暗黙の last ではない。両者は
    返り値では区別できないので、台帳に行があることそのものを表明する。
    """
    declared = {c.name for c in csv_schema.VALUE_COLUMN_LEDGER}

    assert [c for c in KNOWN_VALUE_COLUMNS if c not in declared] == []
    # 未知列は従来どおり last（既存挙動の温存）。
    assert csv_schema.agg_for("a_column_the_ledger_does_not_know") == "last"


def test_the_spread_is_written_as_an_integer_and_prices_as_floats() -> None:
    """CSV へ書く型: spread は int・価格と件数は float（S-9 の型の唯一源）。"""
    assert csv_schema.cast_for("spread")(71.0) == 71
    assert isinstance(csv_schema.cast_for("spread")(71.0), int)
    assert isinstance(csv_schema.cast_for("close")(71), float)


# =====================================================================
# S-3: 2 面の一致（列別集約 == スカラ二項面の畳み込み）
# =====================================================================

def _column_values(column: str) -> "list[float]":
    """検定用の値列（first と last が区別でき、``min`` / ``max`` も一意な並び）。"""
    return [5.0, 2.0, 9.0, 4.0, 7.0, 1.0, 8.0]


@pytest.mark.parametrize("column", KNOWN_VALUE_COLUMNS)
@pytest.mark.parametrize("split", [1, 3, 6])
def test_the_column_aggregation_equals_folding_the_scalar_combine(column: str, split: int) -> None:
    """``groupby.agg`` の結果 == ``combine`` を任意の分割で畳んだ結果。

    2 面（pandas の列別集約 / スカラ二項面）は同じ縮約の別の面でなければならない。
    片方だけを直すとチャンク分割の仕方で値が変わる（carry-over の正しさが崩れる）。
    分割位置を 3 点変えても同じ値になることが、二項面の結合性の表明でもある。
    """
    values = _column_values(column)

    # 面 A: pandas の列別集約（1 つの period に属する全行をまとめて畳む）。
    frame = pd.DataFrame({column: values}, index=[pd.Timestamp("2020-01-06")] * len(values))
    aggregated = frame.groupby(level=0).agg({column: csv_schema.agg_for(column)})[column].iloc[0]

    # 面 B: 前半・後半へ分けて各々を畳み、その 2 つを再び畳む（carry-over と同じ形）。
    combine = csv_schema.combine_for(column)
    head = functools.reduce(combine, values[:split])
    tail = functools.reduce(combine, values[split:])
    folded = combine(head, tail)

    assert folded == pytest.approx(aggregated)


# =====================================================================
# S-4: 導出値が現在と同一（既存データの書式不変）
# =====================================================================

def test_the_value_column_order_is_unchanged() -> None:
    """既知値列の順序は OHLCV → up/dn → spread（spread は up/dn の後）。"""
    assert csv_schema.VALUE_COLUMNS == [
        "open", "high", "low", "close", "volume", "up", "dn", "spread",
    ]
    assert csv_schema.header_columns() == csv_schema.VALUE_COLUMNS


def test_the_sum_columns_are_unchanged_and_still_contain_vol() -> None:
    """``SUM_COLUMNS`` は内容・順序とも不変（vol を落とさない）。

    vol は ``VALUE_COLUMNS`` に無い（ヘッダへ出ない）ため、台帳を ``VALUE_COLUMNS``
    だけから組むと sum 規則が黙って消える。台帳は vol の行を持つこと。
    """
    assert csv_schema.SUM_COLUMNS == ["volume", "vol", "up", "dn"]


def test_the_legacy_header_is_byte_unchanged() -> None:
    """既存 CSV のヘッダ（date + OHLCV）は 1 バイトも変わらない。"""
    assert csv_schema.HEADER == ["date", "open", "high", "low", "close", "volume"]
    assert csv_schema.OHLCV_COLUMNS == ["open", "high", "low", "close", "volume"]


def test_the_columns_that_never_reach_the_header_are_excluded() -> None:
    """``in_header=False`` の列（vol）はヘッダ列に現れない。"""
    assert "vol" not in csv_schema.header_columns()
    assert "vol" in {c.name for c in csv_schema.VALUE_COLUMN_LEDGER}


# =====================================================================
# S-13: 方向内訳は「条件の一致」ではなく台帳の宣言で決まる
# =====================================================================

def test_the_direction_breakdown_columns_are_declared_by_the_ledger() -> None:
    """``UPDOWN_COLUMNS`` は台帳が方向内訳と宣言した列（up/dn）だけ・順序も不変。"""
    assert csv_schema.UPDOWN_COLUMNS == ["up", "dn"]
    assert [c.name for c in csv_schema.VALUE_COLUMN_LEDGER if c.updown] == ["up", "dn"]


def test_a_new_optional_sum_column_does_not_become_a_direction_breakdown(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """任意・合算・ヘッダ有りだが方向内訳ではない列を足しても方向内訳にならない。

    是正前の導出は「``in_header`` かつ ``not required`` かつ ``agg == "sum"``」という
    **条件の一致**だった。この一致は偶然であり、実在する変更要求と衝突する
    （``tools/pseudo_vwap/data.py`` が pv 等を ``SUM_COLUMNS`` へ入れる予定と宣言
    している）。実測（是正前の導出へ仮の pv を 1 行足した反事実・段階 3b 工程 4）:
    導出は ``['up', 'dn', 'pv']`` を返し、``marketdata.tick_m1`` と
    ``marketdata.tools.dedupe_tick_m1`` が pv を方向内訳として扱い始める。
    このとき既存検定は**落ちる**（新規 2 ファイル＝本ファイルと
    ``marketdata/tests/test_rollup_spread_aggregation.py`` を除いて 9 件。実データ待ちで
    baseline から落ちている ISSUE-517 の 1 件は差し引いてある）。ただし落ち方は
    すべて**列形の食い違い**である: 期待した列一覧に pv が 1 つ多い・書かれたヘッダの
    byte 不一致・列数超過の検出が緩んで例外が出ない。どれも「方向内訳へ混入した」ことは
    **告げない**。落ちた検定を読んでも分かるのは列が 1 つ増えたことだけなので、概念は
    条件の一致からではなく台帳の宣言から引く。
    """
    hypothetical = csv_schema.ValueColumn(
        "pv", "sum", csv_schema.combine_for("volume"), float,
    )
    monkeypatch.setattr(
        csv_schema, "VALUE_COLUMN_LEDGER",
        (*csv_schema.VALUE_COLUMN_LEDGER, hypothetical),
    )

    assert csv_schema.updown_columns() == ["up", "dn"]
    # 合算列・ヘッダ列としては従来どおり扱われる（区別しているのは方向内訳だけ）。
    assert "pv" in [c.name for c in csv_schema.VALUE_COLUMN_LEDGER if c.agg == "sum"]
    assert "pv" in csv_schema.header_columns()


# =====================================================================
# 計算量（Test Spy・発行 − 使用 = 0・台帳の長さ 2 点・回数は焼き込まない）
# =====================================================================

class _CountingColumn:
    """台帳 1 行のプロキシ。属性の参照回数を記録する（Test Spy）。"""

    def __init__(self, inner: "csv_schema.ValueColumn", log: "list[str]") -> None:
        self._inner, self._log = inner, log

    def __getattr__(self, name: str):
        self._log.append(name)
        return getattr(self._inner, name)


@pytest.mark.parametrize("extra_rows", [0, 8])
def test_the_direction_breakdown_is_derived_in_a_single_pass(
        extra_rows: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-5: 台帳の走査 − 台帳の行数 = 0・名前の取得 − 出力列数 = 0（台帳長 2 点で不変）。

    導出が行ごとに台帳を引き直したり、該当しない行の名前まで取り出したりしていない
    ことを固定する。出力（``["up", "dn"]``）は正しいまま浪費だけが起きる形なので、
    値の検査では原理的に落ちない。回数そのものは焼き込まない（台帳長で表明する）。
    """
    padding = tuple(
        csv_schema.ValueColumn(f"x{i}", "sum", csv_schema.combine_for("volume"), float)
        for i in range(extra_rows)
    )
    ledger = csv_schema.VALUE_COLUMN_LEDGER + padding
    log: "list[str]" = []
    monkeypatch.setattr(
        csv_schema, "VALUE_COLUMN_LEDGER",
        tuple(_CountingColumn(c, log) for c in ledger),
    )

    derived = csv_schema.updown_columns()

    assert derived == ["up", "dn"]
    assert log.count("updown") - len(ledger) == 0
    assert log.count("name") - len(derived) == 0
