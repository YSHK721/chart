"""3 経路が値列台帳から畳み方を導出する（ISSUE-511 前提 (b) 段階 3b）。

検定する主張は「3 経路が台帳から導出している」ことであり、合成データで 3 関数を直接叩く。
「畳み方が min である」ことそのもの（主張 A）は MT5 の実素材が
``simulator/tests/unit/test_mt5_higher_timeframe_spread_is_min.py`` で固定しており、
ここでは書き直さない。

固定するもの:
    S-5  :func:`marketdata.resample.resample_ohlc` が spread を min で畳む。
    S-6  :func:`marketdata.rollup._merge_agg` が spread を min で畳む。
    S-7  :func:`marketdata.rollup.merge_same_period` が spread を**運ぶ**（従来は辞書
         リテラル 5 キーしか持たず列ごと消えていた）。かつ結合的である。
    S-8  増分更新の結果が全件再構築と一致する（spread 列つき）。
    S-9  spread が**整数として**書かれる（``71`` であって ``71.0``）。
    S-10 列順（spread は up/dn の後）。
    S-11 spread を持たない入力の出力が**是正前と byte 一致**する（3 経路すべて）。
    S-12 ``rollup.py`` に列名の手書き列挙が残っていない。
    S-11b 本番の列形（8 列＝date + OHLCV + up/dn）でも 3 経路すべてが byte 一致する
          （S-11 が凍結しているのは spread を持たない最小の 6 列形であって本番の列形ではない）。
    S-9b `csv_schema.cast_for` の適用点 3 箇所（`rb._bar_to_dict` / `rb._bar_to_csv_row` /
          `rb._write_rollup_df`）それぞれに検出力を与える。1 点ずつ撤去する変異で、対応する
          1 件が落ちる（実測）。3 点は一部で互いを遮蔽するため観測点を経路ごとに分ける。
    S-14 spread 列が増えた直後の遷移（rewrite 1・append 0 → 以後 append）を**再現して**固定する。

計算量（発行 − 使用 = 0・2 点以上・回数は焼き込まない）:
    C-1 ``agg_for`` の引き回数 − 集約した列数 = 0。
    C-2 combine の発行 − 出力バーの列数 = 0。
    C-3 ``RollupWriter.write`` の発行 − CSV データ行数 = 0（spread 列つきでも）。
    C-4 増分経路が全件 rewrite へ落ちない（書いた行 − 要る行 = 0）。
"""
from __future__ import annotations

import ast
import csv as _csv
import functools
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from marketdata import csv_schema
from marketdata import resample as md_resample
from marketdata import rollup as rb

_ROLLUP_SRC = Path(rb.__file__)


# --------------------------------------------------------------------------- #
# 合成 1 分足（決定論・実データを読まない）
# --------------------------------------------------------------------------- #
def _synthetic_m1(start: str, minutes: int, *, with_spread: bool = False) -> pd.DataFrame:
    """連続する 1 分足。``with_spread`` のとき int64 の spread 列を持つ。"""
    idx = pd.date_range(start, periods=minutes, freq="1min")
    base = list(range(minutes))
    data: "dict[str, Any]" = {
        "open": [100.0 + b for b in base],
        "high": [100.0 + b + 0.5 for b in base],
        "low": [100.0 + b - 0.5 for b in base],
        "close": [100.0 + b + 0.2 for b in base],
        "volume": [1.0 + (b % 7) for b in base],
    }
    spread = {"spread": pd.Series([70 + (b % 5) for b in base], index=idx, dtype="int64")}
    return pd.DataFrame({**data, **spread} if with_spread else data, index=idx)


def _write_m1_csv(path: Path, df: pd.DataFrame) -> None:
    """M1 CSV を書く（列は df の実在列から導出＝列名をここに書き写さない）。"""
    cols = list(df.columns)
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["date", *cols])
        for ts, row in df.iterrows():
            w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), *(row[c] for c in cols)])


def _rollup_rows(path: Path) -> "list[list[str]]":
    """ロールアップ CSV のデータ行（文字列のまま＝書かれた表記を見る）。"""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(_csv.reader(fh))[1:]


def _data_row_count(path: Path) -> int:
    return len(_rollup_rows(path))


# =====================================================================
# S-5 / S-6 / S-7: 3 経路が spread を min で畳む
# =====================================================================

def test_resample_folds_the_spread_with_min() -> None:
    """経路 1（全件 resample）: 1 時間足の spread がその 1 時間の最小値になる。"""
    df = _synthetic_m1("2020-01-06 00:00:00", 120, with_spread=True)

    out = md_resample.resample_ohlc(df, "1h")

    # 70 + (b % 5) の最小は 70（各時間に必ず現れる）。最終値なら 74 系・合算なら桁が違う。
    assert list(out["spread"]) == [70, 70]
    assert list(out["open"]) == [100.0, 160.0]      # 既存列の畳み方は不変


def test_merge_agg_folds_the_spread_with_min() -> None:
    """経路 2（既存 CSV と新規 tail のマージ）: 列別集約が spread を min にする。"""
    agg = rb._merge_agg(["open", "high", "low", "close", "volume", "up", "dn", "spread"])

    assert agg["spread"] == "min"
    assert agg["volume"] == "sum"
    assert agg["open"] == "first"


def test_merge_same_period_carries_the_spread_and_folds_it_with_min() -> None:
    """経路 3（チャンク跨ぎ carry-over）: spread を**運び**、min で畳む。

    従来ここは辞書リテラル 5 キー（OHLCV）＋ up/dn ループだけで、spread は列ごと消えた。
    消えた列は下流で「最初から無かった」ように見えるので、値の検査では捕まらない。
    """
    prev_bar = {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 10.0, "spread": 75}
    new_bar = {"open": 2.0, "high": 4.0, "low": 1.5, "close": 3.0, "volume": 20.0, "spread": 71}

    merged = rb.merge_same_period(prev_bar, new_bar)

    assert merged["spread"] == 71
    assert merged == {"open": 1.0, "high": 4.0, "low": 0.5, "close": 3.0,
                      "volume": 30.0, "spread": 71}


def test_merge_same_period_is_associative_with_the_spread_column() -> None:
    """``merge(merge(a,b),c) == merge(a,merge(b,c))``（carry-over の正しさの根拠）。"""
    a = {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 10.0, "spread": 75}
    b = {"open": 2.0, "high": 9.0, "low": 1.5, "close": 3.0, "volume": 20.0, "spread": 71}
    c = {"open": 3.0, "high": 4.0, "low": 0.1, "close": 5.0, "volume": 30.0, "spread": 73}

    left = rb.merge_same_period(rb.merge_same_period(a, b), c)
    right = rb.merge_same_period(a, rb.merge_same_period(b, c))

    assert left == right
    assert left["spread"] == 71


def test_merge_same_period_still_refuses_a_bar_without_a_required_column() -> None:
    """必須列を欠いた bar は従来どおり :class:`KeyError`（黙って列を落とさない）。"""
    complete = {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 10.0}

    with pytest.raises(KeyError):
        rb.merge_same_period(complete, {"open": 2.0})


def test_an_optional_column_is_carried_only_when_both_bars_have_it() -> None:
    """任意列は両方が持つときだけ運ぶ（片方だけの列を捏造しない）。"""
    with_spread = {"open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 10.0, "spread": 75}
    without = {"open": 2.0, "high": 4.0, "low": 1.5, "close": 3.0, "volume": 20.0}

    merged = rb.merge_same_period(with_spread, without)

    assert "spread" not in merged


# =====================================================================
# S-8 / S-9 / S-10: 出力（増分一致・整数表記・列順）
# =====================================================================

def test_incremental_update_equals_a_full_rebuild_with_the_spread_column(tmp_path: Path) -> None:
    """S-8: 増分更新の結果が全件再構築と一致する（spread 列つき）。"""
    inc_dir, full_dir = tmp_path / "inc", tmp_path / "full"
    inc_dir.mkdir(), full_dir.mkdir()
    inc_m1, full_m1 = inc_dir / "m1.csv", full_dir / "m1.csv"

    _write_m1_csv(inc_m1, _synthetic_m1("2020-01-06 00:00:00", 180, with_spread=True))
    state = rb.stream_build(inc_m1, ["1h"], inc_dir / "out", "ref", chunk_rows=100)
    _write_m1_csv(inc_m1, _synthetic_m1("2020-01-06 00:00:00", 300, with_spread=True))
    rb.incremental_update(inc_m1, state, ["1h"], inc_dir / "out", "ref")

    _write_m1_csv(full_m1, _synthetic_m1("2020-01-06 00:00:00", 300, with_spread=True))
    rb.stream_build(full_m1, ["1h"], full_dir / "out", "ref", chunk_rows=100)

    assert (inc_dir / "out" / "ref_1h.csv").read_bytes() == \
        (full_dir / "out" / "ref_1h.csv").read_bytes()


def test_the_spread_is_written_as_an_integer_when_periods_are_empty(tmp_path: Path) -> None:
    """S-9: 休場（空き期間）を挟んでも spread は ``71`` と書かれる（``71.0`` ではない）。

    実測（pandas 3.0.3）: ``resample().agg()`` は空き期間を NaN で埋めるため int64 の列が
    float64 へ**昇格**する。dropna は昇格の後に走るので取り消せない。dtype に頼らず
    書く直前に台帳の cast で型を戻すことを、空き期間のある素材で固定する。
    """
    # 00:00-00:29 と 05:00-05:29 だけ（間の 1 時間足は空＝NaN 埋めが起きる）。
    head = _synthetic_m1("2020-01-06 00:00:00", 30, with_spread=True)
    tail = _synthetic_m1("2020-01-06 05:00:00", 30, with_spread=True)
    m1 = tmp_path / "m1.csv"
    _write_m1_csv(m1, pd.concat([head, tail]))

    rb.stream_build(m1, ["1h"], tmp_path / "out", "ref", chunk_rows=100)

    # 列位置は**実際に書かれたヘッダ**から引く（この素材は up/dn を持たないため、
    #   台帳の全列での位置とはずれる）。
    path = tmp_path / "out" / "ref_1h.csv"
    spread_col = (rb._header_of(path) or []).index("spread")
    written = [row[spread_col] for row in _rollup_rows(path)]

    assert written == ["70", "70"]


def test_the_spread_column_is_written_after_up_and_dn(tmp_path: Path) -> None:
    """S-10: ロールアップ CSV の列順は date + OHLCV → up/dn → spread。"""
    df = _synthetic_m1("2020-01-06 00:00:00", 120, with_spread=True)
    df = df.assign(up=[1.0] * len(df), dn=[2.0] * len(df))
    m1 = tmp_path / "m1.csv"
    _write_m1_csv(m1, df)

    rb.stream_build(m1, ["1h"], tmp_path / "out", "ref", chunk_rows=100)

    assert rb._header_of(tmp_path / "out" / "ref_1h.csv") == [
        "date", "open", "high", "low", "close", "volume", "up", "dn", "spread",
    ]


# =====================================================================
# S-11: spread を持たない入力の出力が是正前と byte 一致（3 経路）
#
# 期待値は**是正前のコードで採取した実バイト列**である（採取時のコミット f8855873）。
# 改行が経路で異なる（csv.writer は CRLF・pandas の to_csv は LF）ことも含めて凍結する。
# 揃えてしまうと既存データの書式が変わる。
# =====================================================================

_LEGACY_ROWS = (
    b"2020-01-06 00:00:00,100.0,159.5,99.5,159.2,234.0",
    b"2020-01-06 01:00:00,160.0,219.5,159.5,219.2,243.0",
    b"2020-01-06 02:00:00,220.0,279.5,219.5,279.2,238.0",
    b"2020-01-06 03:00:00,280.0,339.5,279.5,339.2,240.0",
)
_LEGACY_HEADER = b"date,open,high,low,close,volume"
_LEGACY_CRLF = _LEGACY_HEADER + b"\r\n" + b"".join(r + b"\r\n" for r in _LEGACY_ROWS)
_LEGACY_LF = _LEGACY_HEADER + b"\n" + b"".join(r + b"\n" for r in _LEGACY_ROWS)
_LEGACY_INCREMENTAL = _LEGACY_CRLF + b"2020-01-06 04:00:00,340.0,399.5,339.5,399.2,242.0\r\n"


# --------------------------------------------------------------------------- #
# 本番の列形（8 列＝date + OHLCV + up/dn）
#
# 6 列形だけを凍結していた間、**本番 M1 の列形はどの byte 検定も見ていなかった**
# （実測: data/marketdata 配下の jp225_tick_m1.csv のヘッダは
#  date,open,high,low,close,volume,up,dn。jp225_tick_bid_m1 / jp225_mt5_m1 も同じ 8 列）。
#
# 期待バイト列の出所: コミット **f8855873（＝是正前）** の
# marketdata/{csv_schema,rollup,resample}.py を展開した木で下記 3 経路を実行して採取した
# 実出力である（自分の実装の出力を期待値にしない）。採取器は採取元が是正前であることを
# 実行時に検査する（f8855873 の csv_schema は `csv_schema.cast_for` を持たない）。
# --------------------------------------------------------------------------- #
_LEGACY8_ROWS = (
    b"2020-01-06 00:00:00,100.0,159.5,99.5,159.2,234.0,60.0,30.0",
    b"2020-01-06 01:00:00,160.0,219.5,159.5,219.2,243.0,60.0,30.0",
    b"2020-01-06 02:00:00,220.0,279.5,219.5,279.2,238.0,60.0,30.0",
    b"2020-01-06 03:00:00,280.0,339.5,279.5,339.2,240.0,60.0,30.0",
)
_LEGACY8_HEADER = b"date,open,high,low,close,volume,up,dn"
_LEGACY8_CRLF = _LEGACY8_HEADER + b"\r\n" + b"".join(r + b"\r\n" for r in _LEGACY8_ROWS)
_LEGACY8_LF = _LEGACY8_HEADER + b"\n" + b"".join(r + b"\n" for r in _LEGACY8_ROWS)
_LEGACY8_INCREMENTAL = (
    _LEGACY8_CRLF + b"2020-01-06 04:00:00,340.0,399.5,339.5,399.2,242.0,60.0,30.0\r\n"
)


def _synthetic_m1_with_updown(start: str, minutes: int) -> pd.DataFrame:
    """本番 M1 と同じ 8 列形（OHLCV の値は :func:`_synthetic_m1` と完全に同一）。

    OHLCV を作り直さず ``assign`` で up/dn を足すだけにしてあるので、8 列形の期待値と
    6 列形の期待値の差は up/dn の 2 列ちょうどになる（採取値の取り違えが目視で分かる）。
    """
    base = list(range(minutes))
    return _synthetic_m1(start, minutes).assign(
        up=[float(b % 3) for b in base], dn=[float(b % 2) for b in base]
    )


def test_stream_build_output_is_byte_identical_for_data_without_spread(tmp_path: Path) -> None:
    """経路 1（stream_build）: spread を持たない既存素材の出力が 1 バイトも変わらない。"""
    m1 = tmp_path / "m1.csv"
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", 240))

    rb.stream_build(m1, ["1h"], tmp_path / "out", "ref", chunk_rows=100)

    assert (tmp_path / "out" / "ref_1h.csv").read_bytes() == _LEGACY_CRLF


def test_full_rewrite_output_is_byte_identical_for_data_without_spread(tmp_path: Path) -> None:
    """経路 2（_write_rollup_df＝全件 rewrite）: 出力が 1 バイトも変わらない。"""
    df = _synthetic_m1("2020-01-06 00:00:00", 240).resample("1h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )

    rb._write_rollup_df(tmp_path, "1h", df, "ref")

    assert (tmp_path / "ref_1h.csv").read_bytes() == _LEGACY_LF


def test_incremental_output_is_byte_identical_for_data_without_spread(tmp_path: Path) -> None:
    """経路 3（incremental_update の追記）: 出力が 1 バイトも変わらない。"""
    m1, out = tmp_path / "m1.csv", tmp_path / "out"
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", 180))
    state = rb.stream_build(m1, ["1h"], out, "ref", chunk_rows=100)
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", 300))

    rb.incremental_update(m1, state, ["1h"], out, "ref")

    assert (out / "ref_1h.csv").read_bytes() == _LEGACY_INCREMENTAL


# =====================================================================
# S-12: rollup.py に列名の手書き列挙が残っていない
# =====================================================================

#: 台帳が所有する列名（これらが ``rollup.py`` に literal で現れたら手書き列挙）。
_LEDGER_NAMES = frozenset(c.name for c in csv_schema.VALUE_COLUMN_LEDGER)

#: 本件（ISSUE-511 段階 3b）の射程外に残る列挙。**免除ではなく可視化**である。
#:
#: tail_gap_report は末尾整合の突合（ISSUE-488）であって書き出し・集約の経路ではなく、
#: 本件が導出化を指示された 7 箇所に含まれない。新しい手書き列挙が書き出し経路へ生えれば
#: この表に無いので落ちる。
_OUT_OF_SCOPE_ENUMERATIONS = frozenset({"tail_gap_report"})


def _column_literals_by_function() -> "dict[str, list[int]]":
    """``rollup.py`` の関数ごとに、台帳の列名と一致する文字列リテラルの行番号を集める。"""
    tree = ast.parse(_ROLLUP_SRC.read_text(encoding="utf-8"))
    found: "dict[str, list[int]]" = {}
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        docstring = ast.get_docstring(fn, clean=False)
        hits = [n.lineno for n in ast.walk(fn)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value in _LEDGER_NAMES and n.value != docstring]
        found.update({fn.name: hits} if hits else {})
    return found


def test_the_write_paths_do_not_enumerate_column_names_by_hand() -> None:
    """書き出し・集約の経路に列名の手書き列挙が残っていない。

    列挙が残ると、台帳へ列が増えたときその経路だけが列を落とす。落ちた経路が
    ヘッダを決めていると、行の列構成と既存ヘッダが食い違う。増分はその食い違いを
    検出して**その 1 回だけ**全件 rewrite へ落ち、ヘッダごと書き直す。次の増分では
    ヘッダが一致して追記へ復帰する（実測: 列が増えた直後の増分で rewrite 1 回・
    append 0 回、以後の増分は rewrite 0 回・append 1 回）。恒久転落はしない。
    失われるのは速さではなく列であり、列を落とした経路の出力は値としては正しげな
    まま下流へ流れる（手書き列挙が実際に CSV を壊した経緯は marketdata/rollup.py
    の該当コメント＝ jp225_tick_1M.csv の破壊）。
    """
    offenders = {name: lines for name, lines in _column_literals_by_function().items()
                 if name not in _OUT_OF_SCOPE_ENUMERATIONS}

    assert offenders == {}, (
        f"列名の手書き列挙が残っています: {offenders}。"
        " marketdata.csv_schema の台帳（header_columns / agg_for / cast_for）へ委譲してください。"
    )


def test_the_enumeration_scan_has_detection_power() -> None:
    """走査が恒真式に退化していない（射程外の既知列挙をちゃんと見つけている）。"""
    assert set(_column_literals_by_function()) == _OUT_OF_SCOPE_ENUMERATIONS


# =====================================================================
# 計算量（Test Spy・発行 − 使用 = 0・回数は焼き込まない）
# =====================================================================

@pytest.mark.parametrize("minutes", [2000, 20000])
def test_the_aggregation_rule_is_asked_once_per_aggregated_column(
        minutes: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-1: ``agg_for`` の引き回数 − 集約した列数 = 0（行数 2 点で不変）。

    行数を増やしても引き回数が増えないこと（オーダーの表明）を 2 点で固定する。
    行ごとに規則を引き直す実装は、出力が正しいままここだけが落ちる。
    """
    df = _synthetic_m1("2020-01-06 00:00:00", minutes, with_spread=True)
    asked: "list[str]" = []
    real = csv_schema.agg_for
    monkeypatch.setattr(csv_schema, "agg_for",
                        lambda col: asked.append(str(col)) or real(col))

    md_resample.resample_ohlc(df, "1h")

    assert len(asked) - len(df.columns) == 0


@pytest.mark.parametrize("bar_columns", [
    ["open", "high", "low", "close", "volume"],
    ["open", "high", "low", "close", "volume", "up", "dn", "spread"],
])
def test_the_scalar_combine_is_issued_once_per_output_column(
        bar_columns: "list[str]", monkeypatch: pytest.MonkeyPatch) -> None:
    """C-2: combine の発行 − 出力バーの列数 = 0（列数 2 点で不変）。"""
    bar = {c: 2.0 for c in bar_columns}
    issued: "list[Any]" = []
    real = csv_schema.combine_for

    def counting(col):
        fn = real(col)
        return lambda prev, new: (issued.append(col), fn(prev, new))[1]

    monkeypatch.setattr(csv_schema, "combine_for", counting)

    merged = rb.merge_same_period(dict(bar), dict(bar))

    assert len(issued) - len(merged) == 0
    assert len(merged) == len(bar_columns)


@pytest.mark.parametrize("chunk_rows", [100, 400])
def test_stream_build_issues_exactly_the_rows_it_outputs_with_spread(
        tmp_path: Path, chunk_rows: int) -> None:
    """C-3: ``write`` の発行 − 出力 CSV データ行数 = 0（spread 列つきでも）。"""
    m1 = tmp_path / "m1.csv"
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", 600, with_spread=True))
    out_dir = tmp_path / f"out{chunk_rows}"
    made: "list[Any]" = []

    class _CountingWriter:
        def __init__(self, out: Path, tf: str, ref_prefix: str) -> None:
            self._inner = rb._RollupWriter(out, tf, ref_prefix)
            self.writes = 0
            made.append(self)

        def write(self, period: Any, bar: "dict[str, Any]") -> None:
            self.writes += 1
            self._inner.write(period, bar)

        def commit(self) -> None:
            self._inner.commit()

        def close(self) -> None:
            self._inner.close()

    rb.stream_build(m1, ["1h"], out_dir, "ref", chunk_rows=chunk_rows,
                    writer_factory=_CountingWriter)

    issued = sum(w.writes for w in made)
    used = _data_row_count(out_dir / "ref_1h.csv")
    assert issued - used == 0
    assert used > 0


def _measure_one_incremental_step(tmp_path: Path, history_minutes: int,
                                  monkeypatch: pytest.MonkeyPatch) -> "dict[str, Any]":
    """履歴 ``history_minutes`` 分のロールアップへ 1 時間ぶん追記し、書き込みを数える。"""
    base = tmp_path / str(history_minutes)
    base.mkdir()
    m1, out = base / "m1.csv", base / "out"
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", history_minutes, with_spread=True))
    state = rb.stream_build(m1, ["1h"], out, "ref", chunk_rows=100)
    before = _rollup_rows(out / "ref_1h.csv")
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", history_minutes + 60,
                                    with_spread=True))

    appended: "list[int]" = []
    rewritten: "list[int]" = []
    real_append, real_rewrite = rb._truncate_append_bars, rb._write_rollup_df
    monkeypatch.setattr(rb, "_truncate_append_bars",
                        lambda path, offset, bars: appended.append(len(bars))
                        or real_append(path, offset, bars))
    monkeypatch.setattr(rb, "_write_rollup_df",
                        lambda out_dir, tf, df, ref_prefix=rb._REF_PREFIX:
                        rewritten.append(len(df)) or real_rewrite(out_dir, tf, df, ref_prefix))

    rb.incremental_update(m1, state, ["1h"], out, "ref")
    monkeypatch.setattr(rb, "_truncate_append_bars", real_append)
    monkeypatch.setattr(rb, "_write_rollup_df", real_rewrite)

    after = _rollup_rows(out / "ref_1h.csv")
    written = sum(appended)
    return {
        "written": written,
        "rewritten": sum(rewritten),
        "header": rb._header_of(out / "ref_1h.csv") or [],
        # 末尾の書き替え範囲より前（＝履歴）が 1 行も動いていないか。
        "history_untouched": before[:len(after) - written] == after[:len(after) - written],
        "rows": len(after),
    }


def test_the_incremental_update_appends_instead_of_rewriting_everything(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-4: 増分経路が全件 rewrite へ落ちない（履歴の行を書き直さない）。

    列が 1 つ増えたとき、ヘッダを決める経路と行を書く経路が別々に列を決めていると、
    両者が食い違う。列が増える前に書かれたロールアップへ増分すると、その 1 回だけ
    全件 rewrite が起きてヘッダごと書き直され、次の増分では追記へ復帰する
    （実測: rewrite 1 回・append 0 回 → 以後 rewrite 0 回・append 1 回）。
    恒久化するのは**導出元が食い違ったまま**の場合だけであり（ISSUE-258 の実害経路）、
    現行は列を決める 4 箇所（``rb._bar_to_csv_row`` / ``rb._header_for_bars`` /
    ``rb._RollupWriter`` の行・``rb._write_rollup_df``）がすべて同じ台帳の
    ``csv_schema.header_columns`` から導出するので起きない。
    出力は正しいままなので、状態検証では原理的に落ちない。

    形成中バーの再計算（冪等な上書き）は浪費ではないので、測るのは「履歴を書き直して
    いないこと」と「書いた行数が履歴の長さで増えないこと」である。履歴の長さ 2 点で
    固定し、回数そのものは焼き込まない。
    """
    short = _measure_one_incremental_step(tmp_path, 600, monkeypatch)
    long = _measure_one_incremental_step(tmp_path, 1200, monkeypatch)

    # 列が運ばれている（運ばれていなければ以下の表明は「落ちない理由」を失う）。
    assert "spread" in short["header"] and "spread" in long["header"]
    # 全件 rewrite へ 1 度も落ちていない。
    assert (short["rewritten"], long["rewritten"]) == (0, 0)
    # 履歴は 1 行も書き直していない。
    assert (short["history_untouched"], long["history_untouched"]) == (True, True)
    # 履歴が 2 倍でも書いた行数は増えない（オーダーの表明）。
    assert long["written"] - short["written"] == 0
    assert short["written"] > 0
    # 素材が実際に伸びている（測定が空振りしていない）。
    assert long["rows"] > short["rows"]


# =====================================================================
# S-11b: 本番の列形（8 列＝date + OHLCV + up/dn）の byte 一致（3 経路）
#
# S-11 が凍結しているのは spread を持たない**最小**の 6 列形であって、本番 M1 の列形では
# ない。本番は up/dn を持つ 8 列であり、任意列の有無で header_for / _header_for_bars の
# 結果が変わる＝別の分岐を踏む。挙動は正しい（差分 0 を独立実測済み）が、その正しさを
# 検定が覆っていなかった。
# =====================================================================

def test_stream_build_output_is_byte_identical_for_the_production_column_shape(
        tmp_path: Path) -> None:
    """経路 1（stream_build）: 本番の 8 列形でも出力が是正前と 1 バイトも変わらない。"""
    m1 = tmp_path / "m1.csv"
    _write_m1_csv(m1, _synthetic_m1_with_updown("2020-01-06 00:00:00", 240))

    rb.stream_build(m1, ["1h"], tmp_path / "out", "ref", chunk_rows=100)

    assert (tmp_path / "out" / "ref_1h.csv").read_bytes() == _LEGACY8_CRLF


def test_full_rewrite_output_is_byte_identical_for_the_production_column_shape(
        tmp_path: Path) -> None:
    """経路 2（``_write_rollup_df``＝全件 rewrite）: 本番の 8 列形でも byte 一致。"""
    df = _synthetic_m1_with_updown("2020-01-06 00:00:00", 240).resample("1h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum", "up": "sum", "dn": "sum"}
    )

    rb._write_rollup_df(tmp_path, "1h", df, "ref")

    assert (tmp_path / "ref_1h.csv").read_bytes() == _LEGACY8_LF


def test_incremental_output_is_byte_identical_for_the_production_column_shape(
        tmp_path: Path) -> None:
    """経路 3（``incremental_update`` の追記）: 本番の 8 列形でも byte 一致。"""
    m1, out = tmp_path / "m1.csv", tmp_path / "out"
    _write_m1_csv(m1, _synthetic_m1_with_updown("2020-01-06 00:00:00", 180))
    state = rb.stream_build(m1, ["1h"], out, "ref", chunk_rows=100)
    _write_m1_csv(m1, _synthetic_m1_with_updown("2020-01-06 00:00:00", 300))

    rb.incremental_update(m1, state, ["1h"], out, "ref")

    assert (out / "ref_1h.csv").read_bytes() == _LEGACY8_INCREMENTAL


# =====================================================================
# S-9b: `csv_schema.cast_for` の適用点 3 箇所それぞれの検出力
#
# 型を決める規則は csv_schema.cast_for の 1 関数だが、**適用点は 3 箇所**ある
# （rollup の `rb._bar_to_dict` / `rb._bar_to_csv_row` / `rb._write_rollup_df`）。3 点は一部で
# 互いを遮蔽するため、経路をまたぐ検定（S-9 の stream_build）では 1 点を撤去しても落ちない。
# 実測（本節を足す前の 68 件に対し 1 点ずつ撤去）: 3 通りとも 68 件が全緑のまま生存した。
# だから観測点を経路ごとに分ける。以下の 3 件は、それぞれ**対応する 1 点だけ**で落ちる。
# =====================================================================

def _gapped_m1_with_spread() -> pd.DataFrame:
    """休場（空き期間）を挟む 1 分足。

    ``resample().agg()`` が空き期間を NaN で埋めるため spread 列が int64 → float64 へ昇格し、
    ``dropna`` の後も float64 のまま残る（＝**欠損なしの float64**）。これが「型を戻さないと
    ``70.0`` と書かれる」条件である。
    """
    return pd.concat([
        _synthetic_m1("2020-01-06 00:00:00", 30, with_spread=True),
        _synthetic_m1("2020-01-06 05:00:00", 30, with_spread=True),
    ])


def test_the_bar_dict_restores_the_integer_type_after_a_float_promotion() -> None:
    """適用点 1（`rb._bar_to_dict`）: bar 辞書の面で整数へ戻す。

    ここが守るのは CSV の表記ではなく **bar 辞書が運ぶ値の型**である。撤去すると
    ``np.float64(70.0)`` が carry-over と writer へ流れる。値は等しいので ``== 70`` では
    捕まらない（型そのものを見る）。
    """
    bars = rb._resample_chunk(_gapped_m1_with_spread(), "1h")

    first = next(iter(bars.values()))

    assert first["spread"] == 70
    assert isinstance(first["spread"], int), f"型が戻っていない: {first['spread']!r}"


def test_the_csv_row_restores_the_integer_type_for_a_float_valued_bar() -> None:
    """適用点 2（``_bar_to_csv_row``）: 行整形の面で整数へ戻す。

    `rb.stream_build` 経由では `rb._bar_to_dict` が先に整数化するため、この適用点の欠落は
    stream_build の出力では観測できない（遮蔽）。float 値を持つ bar を行整形へ直接渡して、
    この 1 点だけを見る。同じ関数は増分の追記（`rb._truncate_append_bars`）でも使う。
    """
    bar = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0,
           "spread": 70.0}

    row = rb._bar_to_csv_row(pd.Timestamp("2020-01-06 00:00:00"), bar)

    assert row[-1] == 70
    assert isinstance(row[-1], int), f"型が戻っていない: {row[-1]!r}"


def test_the_full_rewrite_writes_the_spread_as_an_integer(tmp_path: Path) -> None:
    """適用点 3（``_write_rollup_df``）: **全件 rewrite 経路**の整数表記。

    この経路は S-9（stream_build）とも追記経路とも別で、是正前はどの検定も見ていなかった。
    撤去すると実測で ``70.0`` が書かれる。条件は**欠損なしの float64** であること: 欠損を
    含む列は ``notna().all()`` が偽で astype を通らないため、撤去の有無に関わらず float の
    まま書かれる（旧行の値を捏造しないための選択）。ここで固定しているのは欠損なしの場合。
    """
    resampled = md_resample.resample_ohlc(_gapped_m1_with_spread(), "1h")
    # 前提の明示（この 2 つが崩れると、この検定は適用点 3 を見ていない）。
    assert str(resampled["spread"].dtype) == "float64"
    assert bool(resampled["spread"].notna().all())

    rb._write_rollup_df(tmp_path, "1h", resampled, "ref")

    path = tmp_path / "ref_1h.csv"
    spread_col = (rb._header_of(path) or []).index("spread")
    assert [row[spread_col] for row in _rollup_rows(path)] == ["70", "70"]


# =====================================================================
# S-14: 列が増えた直後の遷移（rewrite 1・append 0 → 以後 append）
#
# C-4 の docstring はこの遷移を実測として述べていたが、C-4 自身は**両測定点とも最初から
# spread 列つき**（＝ヘッダ常時一致）で、遷移を再現していなかった。主張は真でも
# 「この検定が固定している」は成り立たない。ここで遷移そのものを測定点にする。
# =====================================================================

def _spy_on_write_paths(monkeypatch: pytest.MonkeyPatch) -> "tuple[list[int], list[int]]":
    """追記（`rb._truncate_append_bars`）と全件 rewrite（`rb._write_rollup_df`）を数える。"""
    appended: "list[int]" = []
    rewritten: "list[int]" = []
    real_append, real_rewrite = rb._truncate_append_bars, rb._write_rollup_df
    monkeypatch.setattr(rb, "_truncate_append_bars",
                        lambda path, offset, bars: appended.append(len(bars))
                        or real_append(path, offset, bars))
    monkeypatch.setattr(rb, "_write_rollup_df",
                        lambda out_dir, tf, df, ref_prefix=rb._REF_PREFIX:
                        rewritten.append(len(df)) or real_rewrite(out_dir, tf, df, ref_prefix))
    return appended, rewritten


def test_a_rollup_written_before_the_column_existed_switches_back_to_appending(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """spread 列が増えた直後だけ全件 rewrite へ落ち、次の増分で追記へ復帰する。

    固定しているのは速さではなく**転落が 1 回で終わること**（恒久化しないこと）である。
    恒久化する形（列の導出元が経路ごとに食い違ったまま）は ISSUE-258 の実害経路であり、
    そのときヘッダと行の列数が食い違って CSV が恒久破壊される。
    """
    m1, out = tmp_path / "m1.csv", tmp_path / "out"

    # 段階 1: spread を持たない M1 でロールアップを作る（ヘッダが 6 列で確定する）。
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", 180))
    state = rb.stream_build(m1, ["1h"], out, "ref", chunk_rows=100)
    assert "spread" not in (rb._header_of(out / "ref_1h.csv") or [])

    appended, rewritten = _spy_on_write_paths(monkeypatch)

    # 段階 2: M1 が spread 列を得た直後の増分 → 追記でなくヘッダごと全件 rewrite。
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", 300, with_spread=True))
    state = rb.incremental_update(m1, state, ["1h"], out, "ref")
    just_after = (len(appended), len(rewritten))
    header_after = rb._header_of(out / "ref_1h.csv") or []

    # 段階 3: さらに追記（ヘッダはもう一致している）→ 追記へ復帰。
    appended.clear()
    rewritten.clear()
    _write_m1_csv(m1, _synthetic_m1("2020-01-06 00:00:00", 360, with_spread=True))
    rb.incremental_update(m1, state, ["1h"], out, "ref")
    next_time = (len(appended), len(rewritten))

    assert just_after == (0, 1), "列が増えた直後は append 0・rewrite 1 のはず"
    assert next_time == (1, 0), "次の増分は append 1・rewrite 0（追記へ復帰）のはず"
    # rewrite がヘッダごと書き直したこと（これが無いと次の増分も一致しない）。
    assert "spread" in header_after
