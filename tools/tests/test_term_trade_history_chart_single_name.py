"""「売買履歴チャート」を 1 つの名前で呼ぶことの固定（依頼者指示 2026-09-27「名前の混在も解消しろ」
「売買履歴チャートにしろ」）。

対象: シミュレーションモードで上部に描くジョブ結果のチャート（`#um-result-chart` の中身）。
    名前は依頼者が決めた「売買履歴チャート」。器は「売買履歴チャートの器」、`.chart-wrap` は「版面」。
    以前はコメントと画面の文言で「上のチャート領域」「上のチャート」「結果チャート」
    「sim 結果チャートの器」「simチャート」が混ざっていた（1 つの対象に 5 つの名前）。

点検は判断を使わない: 別名の一覧を文字列で探す。読み返しで探さない。
引用は別扱い: 「」の中は発言の引用なので、書き換えず検査からも外す。
"""
from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 売買履歴チャートの別名（これが本文に出たら名前の混在）。
_ALIASES = ("上のチャート", "結果チャート", "結果の器", "結果用の器", "simチャート")

#: 売買履歴チャートの記述が在る場所（配信される JS・HTML と sim の Python）。
_SCOPES = (
    ("unified_ui/web/js", "*.js"),
    ("unified_ui/web/tests", "*.js"),
    ("unified_ui/web", "index.html"),
    ("simulator/sim_ui/web/js", "*.js"),
    ("simulator/sim_ui/web/tests", "*.js"),
    ("simulator/sim_ui", "*.py"),
    ("indigators/indicator_ui/web/js/public", "*.js"),
    ("indigators/indicator_kit/web/js", "*.js"),
)

_QUOTATION = re.compile(r"「[^」]*」")


def _files() -> "list[pathlib.Path]":
    out = []
    for base, pattern in _SCOPES:
        root = _ROOT / base
        found = root.glob(pattern) if pattern == "index.html" else root.rglob(pattern)
        out.extend(p for p in found if "node_modules" not in p.parts and p.is_file())
    return sorted(set(out))


def _aliases_in(text: str) -> "list[tuple[int, str]]":
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        body = _QUOTATION.sub("", line)
        hits.extend((number, alias) for alias in _ALIASES if alias in body)
    return hits


def test_the_scanned_set_is_not_empty_and_contains_the_sim_chart_view() -> None:
    # 走査が空振りすると以降が恒真になる。
    names = {p.name for p in _files()}
    assert "sim_result_chart_view.js" in names
    assert "result_chart_area_view.js" in names


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("// ジョブ結果を上のチャート領域へ描く", [(1, "上のチャート")]),
        ("// 依頼者指示「上のチャートが結果を表示」", []),
        ("// ジョブ結果を売買履歴チャートへ描く", []),
        ("// ジョブ結果を simチャートへ描く", [(1, "simチャート")]),
    ],
)
def test_the_detector_finds_aliases_outside_quotations(text, expected) -> None:
    assert _aliases_in(text) == expected


def test_no_alias_of_the_trade_history_chart_remains() -> None:
    found = {
        str(p.relative_to(_ROOT)): hits
        for p in _files()
        if (hits := _aliases_in(p.read_text(encoding="utf-8")))
    }
    assert found == {}, f"売買履歴チャートの別名が残っている（名前は「売買履歴チャート」に統一）: {found}"
