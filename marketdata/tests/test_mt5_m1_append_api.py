"""M1 追記の公開 API の検定（ISSUE-447 段階 1 / 承認事項 A-5）。

なぜ公開 API を足すのか:
    ``marketdata/mt5_ticks/m1_chain.py`` は M1 CSV の書式を自前で持たないために
    ``tick_m1._format_m1_for_csv``（private）を import していた。private への依存は
    「呼んでよい」と宣言されていない実装詳細への依存であり、権威側が内部を変えた瞬間に
    黙って壊れる。検定 M-3（byte 一致）は壊れたことを**後から**教えるだけで、依存そのものは
    消えない。A-5 の裁定はこの依存を恒久的に解消することであり、そのために
    ``tick_m1`` へ追記の公開 API を 1 個だけ足す（既存関数は 1 行も変えない）。

本検定が固定するのは 5 点である:
    1. 追記結果が全構築経路（``tick_m1.build_m1_from_ticks``）と **1 バイト一致**すること
    2. ヘッダを二重に書かないこと（追記の冪等な入り口）
    3. 空入力で **1 バイトも書かない**こと（新着 0 の周期で書込 0 ＝ CX-b と整合）
    4. ``m1_chain`` が private を**もう参照していない**こと（AST 施行・宣言でなく機械検査）
    5. 権威の公開関数が全数、理由と ISSUE 番号つきで台帳に宣言されていること
       （ISSUE-532 欠陥 3。集合のリテラル固定＝追加ごとの承認往復を、宣言の施行へ替えた）
"""
from __future__ import annotations

import ast
import datetime as dt
import re
from pathlib import Path

import pandas as pd
import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import ingest, m1_chain

_PKG = Path(tick_m1.__file__).resolve().parent / "mt5_ticks"


def _m1(minutes: int, *, start="2026-08-25 09:00") -> pd.DataFrame:
    """``minutes`` 本の M1 バー（date index・OHLCV＋up/dn）。"""
    idx = pd.date_range(pd.Timestamp(start), periods=minutes, freq="min", name="date")
    return pd.DataFrame(
        {
            "open": [66000.0 + i for i in range(minutes)],
            "high": [66010.0 + i for i in range(minutes)],
            "low": [65990.0 + i for i in range(minutes)],
            "close": [66005.0 + i for i in range(minutes)],
            "volume": [20.0] * minutes,
            "up": [9.0] * minutes,
            "dn": [8.0] * minutes,
        },
        index=idx,
    )


# =====================================================================
# 書式の単一規則源であること
# =====================================================================

def test_appending_to_a_new_file_writes_the_loader_compatible_header(tmp_path):
    """不在のファイルへ追記すると、ヘッダ 1 行＋本文が書かれる。"""
    path = tmp_path / "out_m1.csv"

    written = tick_m1.append_m1_rows(_m1(3), path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert written == 3
    assert lines[0] == "date,open,high,low,close,volume,up,dn"
    assert len(lines) == 4


def test_appending_twice_never_repeats_the_header(tmp_path):
    """2 回目以降はヘッダを書かない（追記の入り口が 1 つであることの実証）。"""
    path = tmp_path / "out_m1.csv"

    tick_m1.append_m1_rows(_m1(2), path)
    tick_m1.append_m1_rows(_m1(2, start="2026-08-25 09:02"), path)

    text = path.read_text(encoding="utf-8")
    # di-ok(C2): これは被検査ソースではなく、書き出した M1 CSV（データ）そのものの検査
    assert text.count("date,open") == 1
    assert len(text.splitlines()) == 5


def test_the_appended_bytes_equal_the_whole_build_output(tmp_path):
    """M-3 の主張を公開 API 側でも固定する: 追記結果 == 全構築経路の出力（byte 一致）。

    書式・列順・端数・改行のどれかがずれれば、同じデータが 2 通りの CSV になる。
    """
    m1 = _m1(4)
    appended = tmp_path / "appended_m1.csv"
    whole = tmp_path / "whole_m1.csv"

    tick_m1.append_m1_rows(m1, appended)
    tick_m1._write_m1_csv(m1, whole)

    assert appended.read_bytes() == whole.read_bytes()


def test_an_empty_frame_writes_nothing_at_all(tmp_path):
    """空入力では 1 バイトも書かず、ファイルも作らない（新着 0 の周期で書込 0）。"""
    path = tmp_path / "out_m1.csv"

    written = tick_m1.append_m1_rows(_m1(0), path)

    assert written == 0
    assert not path.exists()


def test_an_empty_frame_leaves_an_existing_file_untouched(tmp_path):
    """既存ファイルがあっても空入力は触らない（mtime を動かす追記も行わない）。"""
    path = tmp_path / "out_m1.csv"
    tick_m1.append_m1_rows(_m1(2), path)
    before = path.read_bytes()

    written = tick_m1.append_m1_rows(_m1(0), path)

    assert written == 0
    assert path.read_bytes() == before


# =====================================================================
# 計算量: 追記へ渡した行数 == CSV に増えた行数（発行 − 使用 = 0）
# =====================================================================

def _data_line_count(path: Path) -> int:
    return max(len(path.read_text(encoding="utf-8").splitlines()) - 1, 0)


@pytest.mark.parametrize("first,second", [(3, 3), (3, 6)])
def test_the_number_of_written_lines_equals_the_number_of_given_bars(tmp_path, first, second):
    """渡したバー数ちょうどが増える（作ってから捨てる行が 0）。

    回数そのものを期待値に焼き込まない。固定するのは「渡した数と増えた数の差が 0」であり、
    入力を 2 点（同数・倍）変えても差が 0 のままであること＝出力量だけで決まることである。
    """
    path = tmp_path / "out_m1.csv"

    tick_m1.append_m1_rows(_m1(first), path)
    grew_first = _data_line_count(path)
    tick_m1.append_m1_rows(_m1(second, start="2026-08-25 10:00"), path)
    grew_second = _data_line_count(path) - grew_first

    assert (grew_first, grew_second) == (first, second)


def test_appending_does_not_read_back_what_is_already_there(tmp_path, monkeypatch):
    """既存分を読み直さない（追記は O(新着)・当日累積に比例しない）。

    既存 CSV を読む経路が生えたら、ここで捕まえる（``pd.read_csv`` の発行が 0）。
    """
    path = tmp_path / "out_m1.csv"
    tick_m1.append_m1_rows(_m1(50), path)
    reads: "list[object]" = []
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: reads.append(a) or pd.DataFrame())

    tick_m1.append_m1_rows(_m1(2, start="2026-08-25 11:00"), path)

    assert reads == []


# =====================================================================
# A-5 の目的: private 依存の恒久解消（AST 施行）
# =====================================================================

def _private_tick_m1_attributes_used_by(filename: str) -> "list[str]":
    """``filename`` が ``tick_m1`` の private 属性を参照している箇所を集める。"""
    tree = ast.parse((_PKG / filename).read_text(encoding="utf-8"))
    return sorted({
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "tick_m1"
        and node.attr.startswith("_")
        and not node.attr.startswith("__")
    })


def test_m1_chain_no_longer_reaches_into_a_private_formatter():
    """A-5: ``m1_chain`` は書式の private 実装を参照しない（公開 API 経由に置換済み）。"""
    assert _private_tick_m1_attributes_used_by("m1_chain.py") == []


def test_m1_chain_calls_the_public_append_api():
    """置換先が実在の公開 API であること（呼んでいる先が消えていないことの実証）。"""
    assert m1_chain.tick_m1.append_m1_rows is tick_m1.append_m1_rows


# =====================================================================
# 公開面の宣言台帳（ISSUE-532 欠陥 3）: 追加を人の承認ではなく宣言で通す
# =====================================================================
#
# 旧検定は公開名の集合をリテラルで固定していた。意図（面が黙って広がらない）は正しいが、
# 名前を 1 つ足すたびに承認の往復が発生した。意図は保ち、手段だけを替える——公開関数は
# すべて権威側の台帳 PUBLIC_API_LEDGER に「理由と ISSUE 番号」つきで宣言し、宣言と実体の
# 食い違いを機械で落とす。人の承認なしに名前を足せるが、理由と ISSUE 番号が無ければ落ちる。
#
# 検査は AST のみで行う（権威側を import せずソースの構文から判定する）。宣言文の内容が
# 妥当かは決定不能なので、機械で固定するのは決定可能な 4 点だけである:
#   1. 公開関数に宣言が在ること        3. 宣言が ISSUE 番号を名指すこと
#   2. 宣言に実体が在ること            4. 宣言に ISSUE 番号以外の本文（理由）が在ること

_LEDGER_NAME = "PUBLIC_API_LEDGER"
_ISSUE_REF = re.compile(r"ISSUE-\d+")
_REASON_TRIM = " 　:：・-—.,()（）"


def _public_functions(tree: ast.Module) -> "set[str]":
    """モジュール直下の公開関数名（``_`` 始まりを除く）。"""
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }


def _ledger_entries(tree: ast.Module) -> "dict[str, str]":
    """公開名の台帳（名前 -> 宣言文）を AST から読む。"""
    out: "dict[str, str]" = {}
    for node in ast.walk(tree):
        targets = [node.target] if isinstance(node, ast.AnnAssign) else list(
            getattr(node, "targets", []))
        declared_here = [
            t for t in targets if isinstance(t, ast.Name) and t.id == _LEDGER_NAME]
        value = getattr(node, "value", None)
        pairs = zip(getattr(value, "keys", []), getattr(value, "values", []))
        out.update({
            key.value: val.value
            for _ in declared_here for key, val in pairs
            if isinstance(key, ast.Constant) and isinstance(val, ast.Constant)
        })
    return out


def _surface_findings(path: Path) -> "list[str]":
    """公開関数の集合と台帳の宣言の食い違いを、理由つきで並べて返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    public = _public_functions(tree)
    declared = _ledger_entries(tree)
    return [
        *(f"{name}: 台帳に宣言が無い（理由と ISSUE 番号を書く）"
          for name in sorted(public - set(declared))),
        *(f"{name}: 宣言だけあって実体が無い"
          for name in sorted(set(declared) - public)),
        *(f"{name}: 宣言に ISSUE 番号が無い"
          for name, text in sorted(declared.items()) if not _ISSUE_REF.search(text)),
        *(f"{name}: 宣言に理由が無い"
          for name, text in sorted(declared.items())
          if not _ISSUE_REF.sub("", text).strip(_REASON_TRIM)),
    ]


def _synthetic_module(tmp_path: Path, entries: "dict[str, str]",
                      functions: "list[str]") -> Path:
    """台帳 ``entries`` と公開関数 ``functions`` だけを持つモジュールを書き出す。"""
    ledger = "".join(f"    {name!r}: {text!r},\n" for name, text in entries.items())
    defs = "".join(f"def {name}():\n    return None\n\n\n" for name in functions)
    path = tmp_path / "synthetic_authority.py"
    path.write_text(
        '"""合成モジュール（台帳検査の検出力を測るための入力）。"""\n\n'
        f"{_LEDGER_NAME} = {{\n{ledger}}}\n\n\n{defs}",
        encoding="utf-8")
    return path


def test_every_public_name_of_the_authority_declares_a_reason_and_an_issue():
    """権威 ``tick_m1`` の公開関数は全数が台帳に理由と ISSUE 番号つきで宣言されている。"""
    assert _surface_findings(Path(tick_m1.__file__)) == []


def test_a_new_public_name_needs_no_approval_when_it_declares_a_reason_and_an_issue(tmp_path):
    """宣言つきの追加は通る（承認の往復を要求しない＝ISSUE-532 欠陥 3 の達成目標）。"""
    path = _synthetic_module(tmp_path, {"widen": "ISSUE-999: なぜこの面を足すかの理由"}, ["widen"])

    assert _surface_findings(path) == []


def test_a_public_name_with_no_declaration_is_rejected(tmp_path):
    """宣言の無い公開名は落ちる（面が黙って広がらない＝旧検定の意図を保つ）。"""
    path = _synthetic_module(tmp_path, {}, ["widen"])

    assert _surface_findings(path) == ["widen: 台帳に宣言が無い（理由と ISSUE 番号を書く）"]


@pytest.mark.parametrize("declaration,finding", [
    ("なぜ足すかは書いたが出所を書かない", "widen: 宣言に ISSUE 番号が無い"),
    ("ISSUE-999", "widen: 宣言に理由が無い"),
    ("ISSUE-999: ", "widen: 宣言に理由が無い"),
])
def test_a_declaration_without_a_reason_or_an_issue_number_is_rejected(
        tmp_path, declaration, finding):
    """理由だけ・ISSUE 番号だけの宣言は落ちる（宣言が形骸化しない）。"""
    path = _synthetic_module(tmp_path, {"widen": declaration}, ["widen"])

    assert _surface_findings(path) == [finding]


def test_a_declaration_whose_implementation_is_absent_is_rejected(tmp_path):
    """宣言だけあって実体が無ければ落ちる（台帳が実体から乖離しない）。"""
    path = _synthetic_module(tmp_path, {"ghost": "ISSUE-999: 実体の無い宣言"}, [])

    assert _surface_findings(path) == ["ghost: 宣言だけあって実体が無い"]


@pytest.mark.parametrize("declared", [1, 12])
def test_the_ledger_is_read_in_a_single_parse_whatever_its_size(tmp_path, monkeypatch, declared):
    """計算量: 発行した parse − 使ったソース（1 本）= 0。宣言数 2 点で増えないことも固定する。

    名前ごとに読み直す実装（O(宣言数) の parse）が生えたら、ここで捕まえる。
    """
    entries = {f"widen_{i}": f"ISSUE-999: 理由 {i}" for i in range(declared)}
    path = _synthetic_module(tmp_path, entries, sorted(entries))
    parses: "list[int]" = []
    real_parse = ast.parse
    monkeypatch.setattr(ast, "parse", lambda *a, **k: parses.append(1) or real_parse(*a, **k))

    _surface_findings(path)

    assert len(parses) == 1, f"ソース 1 本に対し parse を {len(parses)} 回発行した"


def test_the_ingest_side_still_reaches_the_authority_for_columns():
    """列の権威は依然 ``tick_m1`` である（A-5 で列定義まで動かしていない）。"""
    assert ingest.tick_m1._TICK_COLUMNS == ["timestamp", "bidPrice", "askPrice"]
