"""型付けが「失敗しない限り捨てられる仕事」を作らない（ISSUE-511 段階 3・工程 5 是正 1/2）。

固定するもの:
    CX-P  **period 文脈の生成 − cast 失敗数 = 0**。列の全値を型付けする面
          （`rb._cast_column`）で、値 1 つ 1 つに period を添える仕事は、cast が失敗して
          「どの列のどの period で壊れたか」を名指すときにしか使われない。失敗が 0 なら
          生成も 0 でなければならない。行数 2 点で固定する。
    CX-M  欠損の無い列の型付けが、その列のメモリを増やさない（行数 2 点）。
    CX-H2 適用点 1（`rb._bar_to_dict` を回す `rb._resample_chunk`）で、型の規則の引き回数
          − 型付けした列数 = 0（バー本数 2 点）。
    CX-H3 適用点 2（`rb._bar_to_csv_row` を回す `rb._RollupWriter`）で同上。
    CX-H4 `rb.stream_build` 全体でも、出力行が増えて引き回数が増えない。
    CX-V  欠損の**無い**列では、値単位の cast 発行 = 0（行数 2 点）。
    S-15  欠損の無い列は台帳の型の dtype のまま返る（＝列ごと配る速い経路が取られる）。
    S-16  列ごとに配れず値単位へ落ちたことが、告げられる面を持つ（無言で転落しない）。
    S-17  見本より列の多い bar でも、caster の持ち上げは書く内容を変えない。

なぜ在るか（是正前の実測・pandas 3.0.3 / numpy 2.4.6 / CPython 3.13.5）:
    `rb._cast_column` は列を**必ず**値単位で回し、値ごとに period を添えていた。period は
    失敗時のメッセージにしか使われないので、成功する限り 1 バイトも出力に現れない。
    そのため状態検証でも、cast の発行数を数える CX-C でも、規則の引き回数を数える CX-H でも
    **原理的に落ちない**。

    測定条件つきの実測（2026-09-17・再測）: 素材は 1,000,000 行 × 8 列
    （open/high/low/close/volume/up/dn/spread・欠損なし・乱数）、各形 5 試行の最小値、
    3 形の出力は byte 一致。環境は aarch64 / pandas 3.0.3 / numpy 2.4.6 / CPython 3.13.5。
        値単位（period 添え・是正前）  2.756 秒
        現行（欠損なし列は列単位 astype） 0.013 秒
        段階 2 前（``notna().all()`` 分岐） 0.014 秒
    ＝値単位は現行の約 200 倍遅い。現行と段階 2 前は欠損の無い列では**同じ経路**（astype）
    を通るため差は測定誤差の内であり、現行が段階 2 前より速いとは言えない（現行の利得は
    速さではなく、欠損の混ざる列を値単位で正しく書けることにある）。
    メモリは 982,272 行 × 5 列（float64・実ロールアップ jp225_m1_5m.csv の行数相当）で
    素材 39.3MB・現行 39.3MB・値単位 157.2MB（4.0 倍・object dtype 化）。
    本モジュールは冒頭で「1 分足を二度と全ロードしない＝OOM を避ける」ことを存在理由に
    掲げており、型付けが素材の 4 倍を常駐させるのはその宣言と正面から食い違う。

    **係数は環境依存である**（同じ素材でも再レビュー環境では 9.313 秒 → 0.133 秒＝50 倍、
    本ファイルの初版では 6.414 秒 → 0.027 秒＝237 倍と測られた）。だからこそ検定は時間を
    閾値にせず**回数**を数える（CLAUDE.md の計算量テスト規約）。上の秒数は動機の記録で
    あって、どの検定の合否条件でもない。

    規則の引き直し（是正 2）も同型である。実測: `rb.stream_build` が
    `csv_schema.cast_for` を引く回数は出力 10 行で 144 回・100 行で 1440 回（行数に比例）。
    `rb._resample_chunk` と `rb._RollupWriter` は、型付けする列が 6 列・出力バーが 4 本の
    ところで 24 回、40 本のところで 240 回引いていた（＝行ループの中で引き直していた）。

射程（誇大にしないための限定）:
    本検定はすべて合成データで組み立て、``data/marketdata/**`` を 1 バイトも読まない。
    ここで固定するのは「捨てられる仕事の不在」であって、出力の正しさではない
    （出力の byte 一致は S-11 / S-11b が別に固定する）。
"""
from __future__ import annotations

import csv as _csv
import logging
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

#: 素材の規模 2 点（行数を 10 倍にする）。回数そのものは焼き込まず、2 点で同じ等式が
#: 成り立つことだけを見る。
_SCALES = [240, 2400]


# --------------------------------------------------------------------------- #
# 合成素材
# --------------------------------------------------------------------------- #
def _hourly_frame(minutes: int) -> pd.DataFrame:
    """1 時間足へ畳んだ frame（全列に欠損が無い）。"""
    return md_resample.resample_ohlc(
        _synthetic_m1("2020-01-06 00:00:00", minutes, with_spread=True), "1h"
    )


def _hourly_frame_with_a_gap(minutes: int) -> pd.DataFrame:
    """1 時間足へ畳んだ frame。先頭 1 本だけ spread が欠損している（他は値を持つ）。

    欠損混在の列（値単位で配る経路）と欠損の無い列（列単位で配れる経路）を 1 つの素材へ
    同居させる。片方だけの素材では、もう片方の経路が測れない。
    """
    frame = _hourly_frame(minutes)
    frame[_SPREAD] = frame[_SPREAD].astype(object)
    frame.loc[frame.index[0], _SPREAD] = np.nan
    return frame


def _written_value_cells(path: Path) -> int:
    """書かれた CSV の値セル（date 列を除く）のうち、空欄でないものの数。"""
    return sum(1 for row in _rollup_rows(path) for cell in row[1:] if cell != "")


def _typed_columns(columns: Any) -> "list[str]":
    """台帳がヘッダへ出すと宣言した列のうち、素材が実際に持つもの（＝型付けする列）。"""
    return [c for c in csv_schema.header_columns() if c in columns]


# --------------------------------------------------------------------------- #
# Test Spy
# --------------------------------------------------------------------------- #
class _CountingCaster:
    """列 1 つぶんの型付けを包み、**period を添えた呼出**だけを数える Test Spy。

    包みは属性を素通しする（`rb._cast_column` が caster の属性を使う実装でも、使わない
    実装でも、同じ包みで測れる）。数えているのは cast の発行そのものではなく、
    **失敗を名指すための文脈を作った回数**である。
    """

    def __init__(self, inner: Any, log: "list[str]", column: Any) -> None:
        self._inner, self._log, self._column = inner, log, column

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("period") is not None:
            self._log.append(str(self._column))
        return self._inner(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _spy_on_period_contexts(
        monkeypatch: pytest.MonkeyPatch) -> "tuple[list[str], list[str]]":
    """（period 文脈の生成簿, cast 失敗簿）を記録する。

    生成は継ぎ目 `rb._cell_caster` の返り値を包んで数え、失敗は失敗の言い方の単一定義
    `rb._cell_cast_error` を包んで数える。両者を別々に数えるので「period を一切添えない」
    退化（＝失敗しても列名と period を名指せない）では等式が成り立たない。
    """
    generated: "list[str]" = []
    failed: "list[str]" = []
    real_caster, real_error = rb._cell_caster, rb._cell_cast_error

    def spying_caster(column: Any) -> Any:
        return _CountingCaster(real_caster(column), generated, column)

    def spying_error(col: Any, value: Any, period: Any) -> Any:
        failed.append(str(col))
        return real_error(col, value, period)

    monkeypatch.setattr(rb, "_cell_caster", spying_caster)
    monkeypatch.setattr(rb, "_cell_cast_error", spying_error)
    return generated, failed


def _spy_on_rule_lookups(monkeypatch: pytest.MonkeyPatch) -> "tuple[list[str], Any]":
    """継ぎ目 `csv_schema.cast_for` を包み、**規則を引いた回数**を記録する。

    返り値は（記録簿, 包む前の実体）。呼び手が測定区間の終わりで実体へ戻すため、
    同じ検定の中で規模を変えて 2 回測っても包みが二重に積み上がらない。
    """
    real = csv_schema.cast_for
    asked: "list[str]" = []

    def spying(column: Any) -> Any:
        asked.append(str(column))
        return real(column)

    monkeypatch.setattr(csv_schema, "cast_for", spying)
    return asked, real


# =====================================================================
# CX-P: period 文脈の生成 − cast 失敗数 = 0
# =====================================================================

@pytest.mark.parametrize("minutes", _SCALES)
def test_the_full_rewrite_attaches_no_period_context_when_no_cast_fails(
        tmp_path: Path, minutes: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-P: 全件 rewrite で、失敗が 0 なら period 文脈の生成も 0。

    period は「どの列のどの period で壊れたか」を名指すためだけに要る。成功する限り
    出力に 1 バイトも現れないので、これを値ごとに作る実装は状態検証でも CX-C でも
    落ちない。固定するのは**無駄の不在**であって回数ではない（行数 2 点で同じ等式）。
    """
    frame = _hourly_frame_with_a_gap(minutes)
    generated, failed = _spy_on_period_contexts(monkeypatch)

    rb._write_rollup_df(tmp_path, "1h", frame, "ref")

    assert len(generated) - len(failed) == 0
    # 測定が空振りしていない（実際に値を書いており、欠損混在の列も持っている）。
    assert _written_value_cells(tmp_path / "ref_1h.csv") > 0
    assert bool(frame[_SPREAD].isna().any())


def test_a_failing_cast_attaches_exactly_one_period_context(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-P（境界）: 失敗 1 件につき period 文脈はちょうど 1 つ。

    「period を一切添えない」実装でも上の検定は 0 − 0 = 0 で通ってしまう。その退化は
    列名と period を添えた失敗（R-6）を壊すので、失敗側でも同じ等式が成り立つことを
    別に見る。ここで固定しているのは、失敗の名指しに要る分**だけ**を作ることである。
    """
    frame = _hourly_frame(240)
    frame[_SPREAD] = frame[_SPREAD].astype(object)
    broken_period = frame.index[1]
    frame.loc[broken_period, _SPREAD] = "abc"
    generated, failed = _spy_on_period_contexts(monkeypatch)

    with pytest.raises(rb.RollupCellCastError) as excinfo:
        rb._write_rollup_df(tmp_path, "1h", frame, "ref")

    assert len(generated) - len(failed) == 0
    # 名指しが実際に行われている（素材の壊れたセルはちょうど 1 つ）。
    assert len(failed) == 1
    assert str(broken_period) in str(excinfo.value)


# =====================================================================
# CX-M: 欠損の無い列の型付けがメモリを増やさない
# =====================================================================

@pytest.mark.parametrize("minutes", _SCALES)
def test_typing_a_gapless_column_does_not_grow_its_memory(minutes: int) -> None:
    """CX-M: 欠損の無い列を台帳の型へ戻しても、その列のメモリは増えない。

    是正前は結果を ``dtype=object`` で固定していたため、値 1 つごとに Python オブジェクトの
    実体とポインタが要り、実測で 2.5 倍（合成・行数 2 点）／3.5 倍（実ロールアップ相当
    982,272 行）へ膨らんでいた。膨らんでも**出力の byte は変わらない**ので、状態検証では
    原理的に落ちない。
    """
    values = _hourly_frame(minutes)["open"]

    typed = rb._cast_column("open", values)

    assert typed.memory_usage(deep=True) - values.memory_usage(deep=True) <= 0
    # 値は 1 つも変わっていない（メモリだけ見て中身が壊れるのを許さない）。
    assert list(typed) == list(values)
    # 前提の明示（欠損混在の列ならこの表明は当てはまらない）。
    assert not bool(values.isna().any())


# =====================================================================
# CX-H2 / CX-H3: 適用点 1・2 でも規則を列につき 1 回しか引かない
# =====================================================================

@pytest.mark.parametrize("minutes", _SCALES)
def test_the_bar_dict_path_asks_the_type_rule_once_per_typed_column(
        minutes: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-H2: `rb._resample_chunk`（行ループ）の引き回数 − 型付けした列数 = 0。

    `rb._bar_to_dict` は 1 バーにつき 1 回呼ばれ、その中で列ごとに規則を引き直していた。
    1 バーの内側では確かに 1 列 1 回だが、**バーのループの外側では成り立たない**。
    実測（是正前）: 型付け列 6 のところ、4 バーで 24 回・40 バーで 240 回。
    """
    chunk = _synthetic_m1("2020-01-06 00:00:00", minutes, with_spread=True)
    typed = _typed_columns(chunk.columns)
    asked, real = _spy_on_rule_lookups(monkeypatch)

    bars = rb._resample_chunk(chunk, "1h")
    monkeypatch.setattr(csv_schema, "cast_for", real)

    assert len(asked) - len(typed) == 0
    # 測定が空振りしていない（バーが複数本あり、型付けする列がある）。
    assert len(bars) > 1
    assert len(typed) > 0


@pytest.mark.parametrize("minutes", _SCALES)
def test_the_csv_row_path_asks_the_type_rule_once_per_typed_column(
        tmp_path: Path, minutes: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-H3: `rb._RollupWriter`（逐次 flush）の引き回数 − 型付けした列数 = 0。

    `rb._bar_to_csv_row` も 1 行につき 1 回呼ばれ、列ごとに規則を引き直していた。
    実測（是正前）: 型付け列 6 のところ、4 行で 24 回・40 行で 240 回。
    """
    chunk = _synthetic_m1("2020-01-06 00:00:00", minutes, with_spread=True)
    bars = rb._resample_chunk(chunk, "1h")
    typed = _typed_columns(chunk.columns)
    asked, real = _spy_on_rule_lookups(monkeypatch)

    writer = rb._RollupWriter(tmp_path / str(minutes), "1h", "ref")
    try:
        for period, bar in bars.items():
            writer.write(period, bar)
        writer.commit()
    finally:
        writer.close()
    monkeypatch.setattr(csv_schema, "cast_for", real)

    assert len(asked) - len(typed) == 0
    # 測定が空振りしていない（実際に複数行を書いている）。
    assert len(_rollup_rows(tmp_path / str(minutes) / "ref_1h.csv")) > 1


def test_stream_build_does_not_ask_the_type_rule_more_often_as_rows_grow(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-H4: 出力行が 10 倍でも `csv_schema.cast_for` の引き回数は増えない。

    実測（是正前）: 出力 10 行で 144 回・100 行で 1440 回（＝書いたセル 1 つあたり 2.4 回）。
    チャンク本数を 1 本に固定して測るので、増えているのが**行数**であることが一意に決まる。
    回数そのものは焼き込まない（2 点の差が 0 であることだけを見る）。
    """
    measured: "dict[int, dict[str, int]]" = {}
    for minutes in _SCALES:
        m1 = tmp_path / f"m1_{minutes}.csv"
        _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", minutes, with_spread=True))
        out = tmp_path / f"out_{minutes}"
        asked, real = _spy_on_rule_lookups(monkeypatch)
        # chunk_rows を素材より大きく取り、チャンク本数を 2 点とも 1 本に揃える。
        rb.stream_build(m1, ["1h"], out, "ref", chunk_rows=1_000_000)
        monkeypatch.setattr(csv_schema, "cast_for", real)
        measured[minutes] = {"asked": len(asked),
                             "rows": len(_rollup_rows(out / "ref_1h.csv"))}

    short, long = measured[_SCALES[0]], measured[_SCALES[1]]
    assert long["asked"] - short["asked"] == 0
    # 測定が空振りしていない（行は実際に増えており、引き回数は 0 ではない）。
    assert long["rows"] - short["rows"] > 0
    assert short["asked"] > 0


# =====================================================================
# S-15 / CX-V: 欠損の無い列では「列ごと配る」速い経路が取られる
#
# CX-C（test_rollup_missing_cell_cast.py）は継ぎ目 `csv_schema.cast_for` を素の Python 関数へ
# 差し替えて cast の発行を数える。その差し替えは `rb._cast_column` の ``values.astype(rule)``
# を ``TypeError`` にするため（実測: ``Cannot interpret '<function ...>' as a data type``）、
# **CX-C の測定中は欠損の無い列も値単位の経路へ落ちる**。CX-C が固定しているのは「空欄へ
# cast を発行しない」ことであって「速い経路が取られる」ことではない。ここでは spy を
# 掛けず、実体の規則のまま速い経路が取られることを固定する。
# =====================================================================

def _ledger_dtype(column: str, values: "pd.Series") -> str:
    """台帳の型で列ごと配ったときの dtype（期待値を検定へ書き写さず実体から引く）。"""
    return str(values.astype(csv_schema.cast_for(column)).dtype)


@pytest.mark.parametrize("column", ["open", _SPREAD])
def test_a_gapless_column_is_typed_column_wise(column: str) -> None:
    """S-15: 欠損の無い列は台帳の型の dtype のまま返る（値単位の object 化を経ない）。

    値単位に配ると結果は ``dtype=object`` になる（`rb._cast_values` の規約）。dtype は
    spy を要さずに観測できる唯一の分岐の跡であり、CX-M が測るメモリ増加の原因でもある。
    """
    values = _hourly_frame(2400)[column]
    # 前提の明示（欠損が混ざる列ではこの表明は当てはまらない）。
    assert not bool(values.isna().any())

    typed = rb._cast_column(column, values)

    assert str(typed.dtype) == _ledger_dtype(column, values)
    assert str(typed.dtype) != "object"
    assert list(typed) == list(values)


class _CountingCalls:
    """caster の**呼出**だけを数える包み（属性は素通しする）。

    規則の実体を素通しするのが要点である。CX-C の spy のように規則そのものを Python 関数へ
    差し替えると ``astype`` が ``TypeError`` で転落し、本番が通らない経路を測ることになる。
    """

    def __init__(self, inner: Any, log: "list[Any]") -> None:
        self._inner, self._log = inner, log

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._log.append(args[0] if args else None)
        return self._inner(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _spy_on_per_value_casts(monkeypatch: pytest.MonkeyPatch) -> "list[Any]":
    """継ぎ目 `rb._cell_caster` を包み、**値 1 つごとの cast 発行**を数える。"""
    issued: "list[Any]" = []
    real = rb._cell_caster
    monkeypatch.setattr(
        rb, "_cell_caster", lambda column: _CountingCalls(real(column), issued)
    )
    return issued


@pytest.mark.parametrize("minutes", _SCALES)
def test_typing_a_gapless_column_issues_no_per_value_cast(
        minutes: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-V: 欠損の無い列では、値単位の cast 発行 = 0（行数 2 点で不変）。

    固定するのは**無駄の不在**であって回数ではない。行数を 10 倍にしても 0 のままである
    ことが、この経路の仕事が行数に比例しないこと（オーダーの表明）である。
    """
    values = _hourly_frame(minutes)["open"]
    issued = _spy_on_per_value_casts(monkeypatch)

    typed = rb._cast_column("open", values)

    assert len(issued) == 0
    # 測定が空振りしていない（列に値が実在する）。
    assert len(typed) == len(values) > 1


def test_the_per_value_spy_counts_the_value_wise_path(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """CX-V（測定の前提）: 同じ spy は、値単位へ落ちる列では値の数だけ数える。

    これが無いと CX-V の 0 は「spy が付いていないから 0」と区別できない（空振りの 0）。
    """
    values = _hourly_frame_with_a_gap(240)[_SPREAD]
    issued = _spy_on_per_value_casts(monkeypatch)

    rb._cast_column(_SPREAD, values)

    assert len(issued) - len(values) == 0
    assert bool(values.isna().any())


def test_the_fall_back_to_the_value_wise_path_is_announced(
        caplog: pytest.LogCaptureFixture) -> None:
    """S-16: 列ごとに配れずに値単位へ落ちたことが、告げられる面を持つ。

    是正前はここが ``except _CAST_FAILURES: pass`` で、転落はどの面にも現れなかった。
    転落先は実測で約 200 倍遅い経路であり（測定条件は本ファイル冒頭）、黙って落ちると
    「遅いが正しい」出力だけが残る＝状態検証でも計算量検定でも原理的に落ちない。

    引き金は台帳の**未知列の規則**である（実測: ``cast_for`` が返す `csv_schema._as_is` は
    Python 関数で、``astype`` は ``Cannot interpret ... as a data type`` を出す）。本番の
    `rb._write_rollup_df` は台帳がヘッダへ出すと宣言した列しか渡さないため未知列は来ない
    ＝**この引き金の本番到達性は 0（解析どまり）**。ここで固定しているのは転落の可視性で
    あって、転落が本番で起きるという主張ではない。
    """
    values = pd.Series([1.0, 2.0, 3.0])

    with caplog.at_level(logging.WARNING, logger=rb.logger.name):
        typed = rb._cast_column("pv", values)

    # 値は 1 つも変わらない（告げるだけで、落ち方そのものは変えない）。
    assert list(typed) == list(values)
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert "pv" in caplog.records[0].getMessage()


def test_a_gapless_column_falls_back_silently_for_nothing(
        caplog: pytest.LogCaptureFixture) -> None:
    """S-16（境界）: 速い経路が取れた列では何も告げない（告知が常時鳴らない）。"""
    values = _hourly_frame(240)["open"]

    with caplog.at_level(logging.WARNING, logger=rb.logger.name):
        rb._cast_column("open", values)

    assert caplog.records == []


# =====================================================================
# S-17: 見本より列の多い bar でも、持ち上げは書く内容を変えない
#
# `rb._caster_of` の fallback（見本に無い列はその場で作る）は、撤去する変異でどの検定も
# 落ちない無検査分岐だった（再レビュー実測 M10）。本番到達性は**解析どまりで 0** である
# （根拠と限定は `rb._caster_of` の docstring）。解析が外れていたときに黙って列が落ちる
# のを防ぐため、振る舞いで固定する。
# =====================================================================

_LIFT_PERIOD = pd.Timestamp("2020-01-06 01:00:00")

#: 見本（6 列）と、それより列の多い bar（8 列）。
_SAMPLE_BAR: "dict[str, Any]" = {
    "open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 10.0,
}
_RICHER_BAR: "dict[str, Any]" = {**_SAMPLE_BAR, "up": 3.0, "dn": 4.0, _SPREAD: 70.0}


def test_a_bar_with_more_columns_than_the_sample_is_written_as_the_bar_itself_would_be(
) -> None:
    """S-17（``csv.writer`` の面）: 見本より列の多い bar の行が、見本を使わない場合と一致する。

    期待値は「見本を bar 自身から作った場合の行」＝持ち上げる前の書き方そのものから引く
    （期待値を検定へ書き写すと、両方が一緒にずれたときに恒真になる）。
    """
    lifted = rb._bar_to_csv_row(_LIFT_PERIOD, _RICHER_BAR, rb._casters_for(_SAMPLE_BAR))

    assert lifted == rb._bar_to_csv_row(_LIFT_PERIOD, _RICHER_BAR)
    # 測定が空振りしていない（見本に無い列が実在し、行にも出ている）。
    assert set(_RICHER_BAR) - set(_SAMPLE_BAR)
    assert len(lifted) == len(_RICHER_BAR) + 1


def test_a_row_with_more_columns_than_the_sample_becomes_the_same_bar_dict() -> None:
    """S-17（bar 辞書の面）: 同じことを `rb._bar_to_dict` の面でも固定する。"""
    row = pd.Series(_RICHER_BAR, name=_LIFT_PERIOD)

    lifted = rb._bar_to_dict(row, rb._casters_for(_SAMPLE_BAR))

    assert lifted == rb._bar_to_dict(row)
    assert set(lifted) - set(_SAMPLE_BAR)
