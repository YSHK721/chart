"""front 定数（曜日順・hold バケット境界）が単一ソースであることの機械検定（ISSUE-502 D-2 / D-3）。

方向: Python（唯一の定義＝``usecase/derive.py``）→ JS 生成物
``web/js/derive_constants_generated.js``。定義を変えたのに
``tools/gen_report_js_constants.py`` を再実行し忘れると、JS だけ古い規則で動き続ける
（ISSUE-253 と同型の「静かなずれ」。曜日順がずれれば front のフィルタが back の集計と
**別の trade** を選び、hold 境界がずれれば保有時間別損益の棒と抽出結果が食い違う。
どちらも例外は出ず画面は正常に見える）。本検定はその再生成漏れを落とす。

さらに「写さない」は宣言では守れない（本リポジトリで繰り返し起きている壊れ方）ため、
利用側（heatmap.js / graphs.js）のソーステキストを読み、**リテラルの再宣言が 0 件**である
ことも機械的に固定する。名前を変えて写しても素通りしないよう、値そのもの（曜日 7 語の並び・
境界秒の並び）を封じる。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from simulator.report_ui.usecase import derive

_REPO = Path(__file__).resolve().parents[4]
_JS_DIR = _REPO / "simulator" / "report_ui" / "web" / "js"
_GENERATED = _JS_DIR / "derive_constants_generated.js"
_GENERATOR = _REPO / "simulator" / "report_ui" / "tools" / "gen_report_js_constants.py"

#: 生成物を読む側（import して使うことが結線として固定されるモジュール）。
_CONSUMERS = {
    "heatmap.js": _JS_DIR / "heatmap.js",
    "graphs.js": _JS_DIR / "graphs.js",
}

#: 写しを持ってはならない front モジュール全部（生成物自身だけが値を持つ）。
#: 既知の 2 消費者だけを見ると、**新しいモジュールが写しを持つ**経路が開いたままになる。
_FRONT_MODULES = {
    p.name: p for p in sorted(_JS_DIR.glob("*.js")) if p.name != _GENERATED.name
}

_WEEK_ARRAY = re.compile(r"export const WEEKORDER = Object\.freeze\(\[([^\]]*)\]\)")
_HOLD_ENTRY = re.compile(r"\{ lo: (\d+), hi: (\d+), label: \"([^\"]+)\" \}")
_REGEN = "定義を変更したら simulator/report_ui/tools/gen_report_js_constants.py を再実行すること"


def _generated_text() -> str:
    return _GENERATED.read_text(encoding="utf-8")


def _parse_weekorder() -> "list[str]":
    body = _WEEK_ARRAY.search(_generated_text())
    assert body is not None, "生成物から WEEKORDER を読み取れません（生成器の書式変更？）"
    return re.findall(r"\"([^\"]+)\"", body.group(1))


def _parse_hold_bounds() -> "list[tuple]":
    return [
        (int(lo), int(hi), label)
        for lo, hi, label in _HOLD_ENTRY.findall(_generated_text())
    ]


# --- ソース走査は述語へ閉じる（assert は判定結果だけを見る）---------------------
#   生のソース文字列に対して assert すると「何を不変条件としたのか」が読み取れない。
#   走査は名前の付いた述語に閉じ、テストはその判定（真偽・違反リスト）だけを固定する。

def _mentions(path: Path, needle: str) -> bool:
    """`path` のソースが `needle` を含むか。"""
    return needle in path.read_text(encoding="utf-8")


def _matches(path: Path, pattern: "re.Pattern[str]") -> bool:
    """`path` のソースが `pattern` に一致する箇所を持つか。"""
    return pattern.search(path.read_text(encoding="utf-8")) is not None


# --- 1. 生成物が現在の Python 定義と一致する（再生成漏れの検出）-----------------

def test_生成JSの曜日順がPython定義と一致する() -> None:
    assert _parse_weekorder() == list(derive.WEEK), _REGEN


def test_生成JSのholdバケット境界がPython定義と一致する() -> None:
    assert _parse_hold_bounds() == [tuple(b) for b in derive.HOLD_BUCKET_BOUNDS], _REGEN


def test_生成JSは全バケットを配る() -> None:
    """パーサが一部しか拾えていないのに一致と誤判定しないための下限固定。"""
    assert len(_parse_hold_bounds()) == len(derive.HOLD_BUCKET_BOUNDS) > 0
    assert len(_parse_weekorder()) == len(derive.WEEK) == 7


def test_生成物であることがファイル冒頭で明示されている() -> None:
    """手編集を誘発しない（tf_ledger_generated.js と同一規約）。"""
    head = _generated_text().splitlines()[0]
    assert "自動生成" in head and "編集しない" in head


def test_生成器が生成物と定義の両方を参照する() -> None:
    missing = [
        needle for needle in ("derive_constants_generated.js", "derive", "HOLD_BUCKET_BOUNDS")
        if not _mentions(_GENERATOR, needle)
    ]
    assert missing == [], f"生成器が参照していません: {missing}"


# --- 2. 利用側に写しが 0 件（手書き複製の再発を遮断）---------------------------

@pytest.mark.parametrize("name", sorted(_CONSUMERS))
def test_利用側は生成物をimportする(name: str) -> None:
    imports_generated = _mentions(_CONSUMERS[name], "./derive_constants_generated.js")
    assert imports_generated, (
        f"{name} が生成物を import していません（写しを持っている疑い）"
    )


#: 曜日配列そのものの並び。名前を変えて写しても素通りしないよう**値ごと**封じる。
_WEEK_LITERAL = re.compile(
    r"\[\s*\"Mon\"\s*,\s*\"Tue\"\s*,\s*\"Wed\"\s*,\s*\"Thu\"\s*,"
    r"\s*\"Fri\"\s*,\s*\"Sat\"\s*,\s*\"Sun\"\s*\]"
)


@pytest.mark.parametrize("name", sorted(_FRONT_MODULES))
def test_frontモジュールは曜日配列のリテラルを持たない(name: str) -> None:
    has_copy = _matches(_FRONT_MODULES[name], _WEEK_LITERAL)
    assert not has_copy, f"{name} に曜日配列の写しがあります"


@pytest.mark.parametrize("name", sorted(_FRONT_MODULES))
def test_frontモジュールはholdバケット境界のリテラルを持たない(name: str) -> None:
    """境界秒とラベルは 1 つでも書かれていれば写しの芽（例: `1e9` / `"10-30m"`）。"""
    path = _FRONT_MODULES[name]
    copies = [
        needle
        for needle in [f'"{lab}"' for _, _, lab in derive.HOLD_BUCKET_BOUNDS] + ["1e9"]
        if _mentions(path, needle)
    ]
    assert copies == [], f"{name} に hold バケット境界の写しがあります: {copies}"


@pytest.mark.parametrize("name", sorted(_FRONT_MODULES))
def test_frontモジュールは生成物と同名の定数を再定義しない(name: str) -> None:
    path = _FRONT_MODULES[name]
    redeclared = [
        symbol for symbol in ("WEEKORDER", "HOLD_BUCKET_BOUNDS")
        if _matches(path, re.compile(rf"(^|\n)\s*(export\s+)?(const|let|var)\s+{symbol}\b"))
    ]
    assert redeclared == [], (
        f"{name} が {redeclared} を再宣言しています（生成物を import すること）"
    )
