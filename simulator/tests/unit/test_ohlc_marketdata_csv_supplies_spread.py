"""supplies_spread（その実体が気配幅の列を供給するか）の検定。ISSUE-511 段階 8-A。

なぜこの境界が要るか:
    列名を知ってよいのは、その列を Bar へ写す読み手だけである。保証境界の側
    （`simulator/main/tester_settings/unsupported.py`）に「ヘッダに spread 列があるか」を
    書くと、列名の所有者が 2 つになる。

**述語は 3 形式すべてに対して正直に答える**（依頼者裁定 2026-09-18）。形式ごとに正しい
区切りでヘッダを割り、その形式での気配幅の列名が在るかを答える:

    marketdata / comma … comma 区切り・列名 spread
    MT5 TAB          … タブ区切り・列名 <SPREAD>

    是正前は comma 区切りだけで割っていたため MT5 TAB が False になっていた。これは
    「MT5 が気配幅を供給しない」という意味ではなく（供給する）、タブ区切りのヘッダを
    comma で割ると列にならないという機構上の帰結にすぎなかった。述語が嘘をつく形を、
    別の条件で覆い隠す（連言にする）のではなく、述語そのものを正す。

固定する不変条件:
    1. 判定材料はヘッダ 1 行の列名だけ（marketdata 9 列 True / 6 列 False）。
    2. comma 形式（`time,…,spread`）は列を持てば True・持たなければ False（TBD-6 は
       Option A で確定・依頼者裁定 2026-09-18）。実測（2026-09-18・本作業ツリー）:
       comma 形式の実体はディスク上 0 件で、発生源はテストと探索用プローブのみ。
    3. MT5 TAB は `<SPREAD>` 列を持てば True・持たなければ False（形式で決め打たない）。
       実データでの True は突合フィクスチャの現物で固定する。
    4. 読めない・形式不明・パスでない（None＝データ非供給の modelling）は False。
    5. 読むのはヘッダ 1 行だけ。形式判定を内側で使っても読取は 1 実体につき 1 回。
    6. 計算量（CX-1）: 発行 − 判定に使った回数 = 0（呼出の継ぎ目とヘッダ読取の継ぎ目の
       両方）。発行数はデータ行数に依らない。
    7. 既に読んだヘッダを渡された形式判定は読み直さない。**「読めなかった」ことを表す
       ``None`` を渡した場合も読み直さない**（_UNREAD が「未読」と「読めなかった」を
       別の値で表す構造の、唯一の機械の結び）。この契約は docstring が宣言していたが、
       段階 8-A の時点では測る検定が 0 件だった（工程 5 レビュー 🟡-1）。
    8. 気配幅の列名表 ``_SPREAD_COLUMN_BY_FORM`` のキー集合は、形式判定が返す語彙の写しで
       ある。写しが腐らないことを機械で結ぶ（同 🟡-2。結びが無いと、形式を 1 つ足しても
       その形式だけ気配幅を見ない配置が黙って通る）。

負の対照の出所（推測しない・実測 2026-09-18 / pandas 3.0.3）:
    本文を「壊す」候補 3 通りのうち、5 行でも 5,000 行でも本文読みが落ちるのは不正な
    UTF-8 バイトだけだった（列数を増やした本文は pandas が先頭列を索引と解して落ちず、
    閉じない引用符は 5 行で落ちるが 5,000 行では落ちない）。よって対照には UTF-8 を使う。

Test Spy は `marketdata/tests/spread_series_fixture.py` の実装を import して使う
（同じ Spy を手書き複製しない）。
"""
from __future__ import annotations

import builtins
from pathlib import Path

import pandas as pd
import pytest

from marketdata.tests.spread_series_fixture import spy
from simulator.adapter.repository import ohlc_marketdata_csv
from simulator.tests.unit.test_ea_bindings_source_declarations_are_bound import (
    _UNKNOWN_FORM,
    returned_string_literals,
)

_ROOT = Path(__file__).resolve().parents[3]
#: MT5 突合フィクスチャ（実 OANDA-Japan MT5 JP225 M1・読み取りのみ）。
_MT5_FIXTURE = (
    _ROOT / "simulator" / "tests" / "fixtures" / "mt5" / "ma_slope_jp225_202501"
    / "input" / "JP225_M1_202501.csv"
)

#: 実データの実測ヘッダ（data/marketdata/jp225_mt5_spread_m1.csv の 1 行目・2026-09-18）。
#: バッククォートで囲まないのは、静的品質検定 C1 の索引が data/marketdata の symlink を
#: 辿らず「存在しない」と報せるためである（実体は head -1 で実測済み）。
_MD9 = "date,open,high,low,close,volume,up,dn,spread"
#: 実データの実測ヘッダ（data/marketdata/jp225_m1.csv の 1 行目・同日）。
_MD6 = "date,open,high,low,close,volume"
#: comma 形式（CsvOHLCRepository の必須列）。
_COMMA = "time,open,high,low,close,volume,spread"
#: 気配幅の列を持たない comma 形式（探索用サンプルと同形）。
_COMMA_NO_SPREAD = "time,open,high,low,close,volume"
#: MT5 エクスポート（タブ区切り・気配幅あり）。
_MT5_TAB = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"
#: 同じタブ区切りだが気配幅の列を持たない実体（形式で決め打っていないことの対照）。
_MT5_TAB_NO_SPREAD = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>"
#: どの形式でもないヘッダ。
_GARBAGE = "foo,bar"
#: 区切りの両側に空白を挟んだ marketdata 9 列（空白は列名の一部ではない）。
#: 列名を割るときに前後の空白を落とさないと、気配幅の列が " spread" になって見つからない。
_MD9_PADDED = "date, open, high, low, close, volume, up, dn, spread "
#: 同じ空白の入れ方をした comma 形式（空白の扱いが 1 形式だけの都合でないことの対照）。
_COMMA_PADDED = "time, open, high, low, close, volume, spread "


def _write(tmp_path, name: str, header: str, body: str = "") -> str:
    path = tmp_path / name
    path.write_text(header + "\n" + body, encoding="utf-8")
    return str(path)


def _md9_rows(n: int) -> str:
    """``_MD9`` の形をした ``n`` 行の本文（値は使われない）。"""
    return "".join(
        "2024-01-08 00:00:00,100.0,101.0,99.0,100.5,10.0,1.0,0.0,71\n" for _ in range(n)
    )


def _first_line_bytes(path) -> bytes:
    """ファイルの 1 行目（改行込み・バイト列）。"""
    with open(path, "rb") as f:
        return f.readline()


# --- 1. 状態検証（R-1）: 形式ごとの気配幅の列が答えを決める ---------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (_MD9, True),
        (_MD6, False),
        (_COMMA, True),
        (_COMMA_NO_SPREAD, False),
        (_MT5_TAB, True),
        (_MT5_TAB_NO_SPREAD, False),
        (_GARBAGE, False),
        (_MD9_PADDED, True),      # 空白は列名の一部ではない（割った列名の前後を落とす）
        (_COMMA_PADDED, True),    # 同上・別形式
    ],
)
def test_the_header_columns_decide_whether_the_entity_supplies_spread(
    tmp_path, header, expected
):
    path = _write(tmp_path, "header.csv", header)
    assert ohlc_marketdata_csv.supplies_spread(path) is expected


def test_the_real_mt5_fixture_supplies_spread():
    assert _MT5_FIXTURE.is_file(), "MT5 突合フィクスチャが無い（前提の崩れ）"
    header = _first_line_bytes(_MT5_FIXTURE).decode("utf-8")
    assert "<SPREAD>" in header   # 前提の実測: この実体は気配幅の列を持つ
    assert ohlc_marketdata_csv.supplies_spread(str(_MT5_FIXTURE)) is True


def test_the_mt5_spread_column_name_is_the_one_the_mt5_reader_requires():
    """MT5 列名の写しが腐らない機械の結び。

    `simulator/adapter/repository/ohlc_mt5_csv.py` は列名の対応表を公開しておらず（必須列の
    私有タプルに持つ）、その改変は本段の範囲外である。したがって本モジュールの持つ名前は
    写しであり、ここが両者を機械で結ぶ（写しが腐ればここが落ちる）。
    """
    from simulator.adapter.repository import ohlc_mt5_csv

    assert ohlc_marketdata_csv._MT5_SPREAD_COLUMN in ohlc_mt5_csv._REQUIRED


def test_an_absent_path_answers_false(tmp_path):
    assert ohlc_marketdata_csv.supplies_spread(str(tmp_path / "no_such.csv")) is False


def test_a_source_that_is_not_a_path_answers_false():
    # データ非供給の modelling（Model=3）は data_path=None で通る（既存経路と同じ縮退）。
    assert ohlc_marketdata_csv.supplies_spread(None) is False


# --- 2. 段階 8-C の単純置換が保証境界を保つこと --------------------------------


@pytest.mark.parametrize(
    ("header", "would_fire"),
    [
        (_MD6, True),             # 気配幅なし marketdata: 従来どおり弾く
        (_MD9, False),            # 気配幅つき marketdata: 8-C で通す（境界解除の目的）
        (_MT5_TAB, False),        # MT5 TAB: 従来どおり通す（指紋 A/B の経路）
        (_COMMA, False),          # 気配幅つき comma: 現在も非発火・不変
        (_COMMA_NO_SPREAD, True),  # 気配幅なし comma: 弾く側へ寄る
    ],
)
def test_the_stage_8c_substitution_would_keep_mt5_tab_unblocked(
    tmp_path, header, would_fire
):
    """8-C で N-17 の述語を「supplies_spread が False」へ単純置換したときの発火可否。

    本段では N-17 を変更しない（呼び手は無い）。ここで固定するのは述語の戻り値だけである。
    """
    path = _write(tmp_path, "entity.csv", header)
    assert (not ohlc_marketdata_csv.supplies_spread(path)) is would_fire


# --- 3. 読取量（R-2）: ヘッダ 1 行だけを読む -------------------------------------


class _CountingFile:
    """呼び手へ渡ったバイト数を数えるファイルの覆い（読取量の継ぎ目）。"""

    def __init__(self, wrapped, counted: "list[int]") -> None:
        self._wrapped = wrapped
        self._counted = counted

    def __enter__(self):
        self._wrapped.__enter__()
        return self

    def __exit__(self, *exc):
        return self._wrapped.__exit__(*exc)

    def readline(self, *args):
        chunk = self._wrapped.readline(*args)
        self._counted.append(len(chunk))
        return chunk

    def read(self, *args):
        chunk = self._wrapped.read(*args)
        self._counted.append(len(chunk))
        return chunk


def _answer_and_delivered_bytes(monkeypatch, path: str) -> "tuple[bool, int]":
    """``path`` を判定し、その判定で呼び手へ渡ったバイト数を返す。"""
    real_open = builtins.open
    counted: "list[int]" = []
    monkeypatch.setattr(
        builtins, "open", lambda *a, **k: _CountingFile(real_open(*a, **k), counted)
    )
    answer = ohlc_marketdata_csv.supplies_spread(path)
    monkeypatch.undo()
    return answer, sum(counted)


def test_it_reads_only_the_header_line_whatever_the_file_size(monkeypatch, tmp_path):
    small = _write(tmp_path, "small.csv", _MD9, _md9_rows(5))
    large = _write(tmp_path, "large.csv", _MD9, _md9_rows(5_000))
    header_bytes = len((_MD9 + "\n").encode("utf-8"))

    small_answer, small_read = _answer_and_delivered_bytes(monkeypatch, small)
    large_answer, large_read = _answer_and_delivered_bytes(monkeypatch, large)

    assert (small_answer, large_answer) == (True, True)   # 空振り防止
    assert small_read == header_bytes
    assert large_read == header_bytes                      # 1,000 倍の本文でも読取は増えない


def test_it_reads_only_the_header_line_of_the_real_mt5_fixture(monkeypatch):
    """形式判定が内側に入っても、実データの読取はヘッダ 1 行のままである。"""
    assert _MT5_FIXTURE.is_file(), "MT5 突合フィクスチャが無い（前提の崩れ）"
    header_bytes = len(_first_line_bytes(_MT5_FIXTURE))

    answer, delivered = _answer_and_delivered_bytes(monkeypatch, str(_MT5_FIXTURE))

    assert answer is True                     # 空振り防止
    assert delivered == header_bytes


def test_it_answers_even_when_the_body_cannot_be_parsed(tmp_path):
    # 本文が不正な UTF-8 の 5,000 行（本文を解釈する読みは同じファイルで落ちる）。
    path = tmp_path / "broken.csv"
    path.write_bytes(
        (_MD9 + "\n").encode("utf-8")
        + b"".join(b"\xff\xfe" + str(i).encode("ascii") + b"\n" for i in range(5_000))
    )
    assert ohlc_marketdata_csv.supplies_spread(str(path)) is True
    with pytest.raises(UnicodeDecodeError):
        pd.read_csv(path)   # 負の対照: 本文まで読む経路は同じ実体で落ちる


# --- 4. 計算量テスト（CX-1）: 発行 − 判定に使った回数 = 0 ------------------------


def _judged(paths: "list[str]") -> "dict[str, bool]":
    """各実体を 1 回ずつ判定して答えを使う（使用 = 相異なる実体の数）。"""
    return {path: ohlc_marketdata_csv.supplies_spread(path) for path in paths}


def _entities(tmp_path) -> "list[str]":
    """答えが割れる 4 実体（marketdata 9 列 / 6 列 / MT5 TAB / 読めない実体）。

    読めない実体を混ぜるのは、形式判定へ「既に読んだヘッダ」を渡す受け渡しが、**読めな
    かったときにも 2 回読みへ退行しない**ことを測るためである。渡す値が「未供給」の
    合図と同じ None になるため、ここを測らないと縮退経路だけが黙って 2 回読む。
    """
    return [
        _write(tmp_path, "a.csv", _MD9, _md9_rows(5)),
        _write(tmp_path, "b.csv", _MD6, _md9_rows(5)),
        _write(tmp_path, "c.csv", _MT5_TAB),
        str(tmp_path / "absent.csv"),
    ]


def _issued_and_used(monkeypatch, tmp_path, rows: int) -> "tuple[int, int]":
    """``rows`` 行の実体 1 つを判定したときの（発行数, 使用数）。"""
    calls = spy(monkeypatch, ohlc_marketdata_csv, "supplies_spread")
    path = _write(tmp_path, f"scale_{rows}.csv", _MD9, _md9_rows(rows))
    answers = _judged([path])
    monkeypatch.undo()
    return len(calls), len(answers)


def test_no_judgment_issues_a_call_its_answer_does_not_use(monkeypatch, tmp_path):
    calls = spy(monkeypatch, ohlc_marketdata_csv, "supplies_spread")
    paths = _entities(tmp_path)

    answers = _judged(paths)

    assert set(answers.values()) == {True, False}          # 空振り防止（答えが割れている）
    assert len(calls) - len(set(paths)) == 0               # 発行 − 使用 = 0


def test_a_judgment_reads_the_header_once_however_many_formats_it_considers(
    monkeypatch, tmp_path
):
    """形式判定を内側で使っても、同じ実体のヘッダ読取は 1 回で済む（構造で保証）。

    「形式を判定するために 1 回 → 列を見るためにもう 1 回」読む形は、出力が 1 ビットも
    変わらないため状態検証では落ちない。ここが唯一その無駄を止める。
    """
    reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")
    paths = _entities(tmp_path)

    answers = _judged(paths)

    assert set(answers.values()) == {True, False}   # 空振り防止
    assert len(reads) - len(set(paths)) == 0        # 読取の発行 − 使用 = 0


def test_the_issued_judgments_do_not_grow_with_the_data(monkeypatch, tmp_path):
    small_issued, small_used = _issued_and_used(monkeypatch, tmp_path, 5)
    large_issued, large_used = _issued_and_used(monkeypatch, tmp_path, 5_000)

    assert small_issued - small_used == 0
    assert large_issued - large_used == 0
    assert large_issued == small_issued   # 行数 1,000 倍でも発行は増えない（回数は固定しない）


# --- 5. 既読ヘッダの受け渡し（R-7）: 渡された行では読み直さない -------------------
#
# ここが _UNREAD（「未読」と「読めなかった」を別の値で表す構造）の唯一の機械の結びである。
# 段階 8-A では docstring が「``None`` を渡しても読み直さない」を契約として宣言しながら、
# それを測る検定が 0 件だった（工程 5 レビュー 🟡-1）。契約に検定が無いと、素朴な ``None``
# 番兵へ戻す改変が**答えを 1 ビットも変えずに**読取だけを増やすため、状態検証では落ちない。


def test_a_header_that_was_already_read_is_not_read_again(monkeypatch, tmp_path):
    """既に読んだヘッダ 1 行を渡された形式判定は、その実体を読まない。

    受け口が効いていることの正の対照（この検定が緑でも _UNREAD の有無は判らない——
    それを測るのは下の「読めなかった」側である）。
    """
    # Arrange
    reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")
    path = _write(tmp_path, "readable.csv", _MD9)

    # Act
    form = ohlc_marketdata_csv.detect_ohlc_form(path, header=_MD9)

    # Assert
    assert form == "marketdata"   # 空振り防止（渡した 1 行だけで判定できている）
    assert len(reads) == 0        # 渡された行で足りる＝読取は 1 回も発行されない


def test_a_header_that_could_not_be_read_is_not_read_again(monkeypatch, tmp_path):
    """**読めなかった**ことを表す ``None`` を渡しても、その実体を読み直さない。

    実体は「読めば marketdata と判る」ものを使う。素朴な ``None`` 番兵（``None`` を「未読」
    の合図に使う版）では、ここで実体を読みに行って ``"marketdata"`` を返し、読取が 1 回
    発行される。**答えと読取の両方がこの 1 件でだけ割れる**ため、この検定が唯一その改変を
    殺す。
    """
    # Arrange
    reads = spy(monkeypatch, ohlc_marketdata_csv, "_header_line")
    path = _write(tmp_path, "readable.csv", _MD9)

    # Act
    form = ohlc_marketdata_csv.detect_ohlc_form(path, header=None)

    # Assert
    assert form == _UNKNOWN_FORM   # 読めなかった実体は判定不能へ倒れる
    assert len(reads) == 0         # 読み直さない（無駄の不在。回数は焼き込まない）


# --- 6. 形式語彙の写しが所有者へ機械で結ばれていること（🟡-2） -------------------
#
# 抽出器は `test_ea_bindings_source_declarations_are_bound.py` の実装を import して使う
# （同じ AST 抽出器を手書き複製しない）。抽出器が空振りしていないことの自己検査は、その
# モジュール側の test_the_extractor_reads_return_literals が持つ。


def _detected_forms() -> "set[str]":
    """形式判定が ``return`` する語彙（書かれている語彙を AST で読む）。"""
    return returned_string_literals(
        Path(ohlc_marketdata_csv.__file__), "detect_ohlc_form"
    )


def test_the_forms_with_a_spread_column_match_the_vocabulary_detect_returns():
    """気配幅の列名表のキー ∪ {形式不明} == 形式判定が返す語彙。

    どちらか片側に形式を足すと落ちる。落ちなければ、足した形式に対して
    ``supplies_spread`` が**黙って False へ倒れる**（気配幅を持つ実体を持たないと答える）
    配置が素通りする。形式不明 "" を宣言表に載せないのは、既定へ倒す合図であって形式では
    ないからである。
    """
    # Arrange / Act
    declared = set(ohlc_marketdata_csv._SPREAD_COLUMN_BY_FORM) | {_UNKNOWN_FORM}
    detected = _detected_forms()

    # Assert
    assert declared == detected, {
        "宣言のみ": sorted(declared - detected),
        "判定のみ": sorted(detected - declared),
    }


def test_neither_side_of_the_form_binding_is_empty():
    """空集合どうしの一致で緑になっていないこと（結びの自己検査）。"""
    # Assert
    assert len(ohlc_marketdata_csv._SPREAD_COLUMN_BY_FORM) > 0
    assert len(_detected_forms()) > 0


def test_the_unknown_form_is_not_a_form_with_a_spread_column():
    """形式不明は気配幅の列名表の要素ではない（載せると False へ倒す合図が形式に化ける）。"""
    # Assert
    assert _UNKNOWN_FORM not in ohlc_marketdata_csv._SPREAD_COLUMN_BY_FORM
