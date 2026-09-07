"""report キー語彙の言語跨ぎ包含検定（ISSUE-502 D-4）。

固定する不変条件:
    back（``BuildReportPayload`` が組む ``segment.report``）が出すキーは、front の語彙表
    （``web/js/glossary.js`` の ``REPORT_VOCAB``）に**必ず載っている**。

なぜ必要か:
    語彙表に無いキーを back が出すと、画面はそれを英語ラベルのまま素通しし、章立てからも
    日本語呼称からも用語解説からも漏れる。**例外は出ない**——項目が 1 つ地味に読めなくなる
    だけで、テストも通り続ける。指標を 1 つ足すたびに起こり得る壊れ方であり、宣言（コメント）
    では防げないので機械的に落とす。

    front 側 3 構造（REPORT_GROUPS / LABELS_JA / GLOSSARY）のキー集合一致は、3 構造を
    ``REPORT_VOCAB`` からの導出に変えたことで**構造的に保証**済み（検定ではなく形で担保）。
    その導出の等価性は front 側の JS 検定
    （simulator/report_ui/web/js/tests/report_vocab.test.mjs）が固定する。

方向について:
    生成物方式（Python→JS）は採らない。語彙表は役割・見方の日本語表示文（約 45 項目・
    表示専用の文言）を持ち、これを usecase 層へ移すのは責務違反である。一方 back 側は
    バックテスト統計値からの整形式であり JS からは生成できない。どちらも他方から導出でき
    ないため、両者を機械的に突合する（精査台帳が挙げた方式 (b)）。
"""
from __future__ import annotations

import re
from pathlib import Path

from simulator.report_ui.tests.unit.test_build_report_payload import (
    _ea_params,
    _make_result,
    _meta,
    _spec,
)
from simulator.report_ui.usecase.build_report_payload import BuildReportPayload

_REPO = Path(__file__).resolve().parents[4]
_GLOSSARY_JS = _REPO / "simulator" / "report_ui" / "web" / "js" / "glossary.js"

_VOCAB_BLOCK = re.compile(
    r"export const REPORT_VOCAB = \[(.*?)\n\];", re.DOTALL
)
_VOCAB_KEY = re.compile(r"\{ key: \"((?:[^\"\\]|\\.)*)\"")


def _vocab_keys() -> "list[str]":
    """front 語彙表のキー列を glossary.js から読み取る。"""
    block = _VOCAB_BLOCK.search(_GLOSSARY_JS.read_text(encoding="utf-8"))
    assert block is not None, "glossary.js から REPORT_VOCAB を読み取れません（書式変更？）"
    return _VOCAB_KEY.findall(block.group(1))


def _back_report_keys() -> "list[str]":
    """back が実際に出す report キー（ソース走査ではなく**実行結果**から採る）。"""
    result = _make_result([100.0, -40.0], [2000, 3000], [10100.0, 10060.0])
    payload = BuildReportPayload().execute_single(
        result=result, bars=[], spec=_spec(), ea_params=_ea_params(), meta=_meta("is"),
    )
    return list(payload.segments["single"].report.keys())


def test_語彙表のキーは重複しない() -> None:
    keys = _vocab_keys()
    dups = sorted({k for k in keys if keys.count(k) > 1})
    assert dups == [], f"REPORT_VOCAB にキーの重複があります: {dups}"


def test_backが出すreportキーはすべて語彙表に載っている() -> None:
    vocab = set(_vocab_keys())
    back = _back_report_keys()
    assert back, "back が report キーを 1 つも出していません（検定が空振りしています）"
    missing = [k for k in back if k not in vocab]
    assert missing == [], (
        "back の report キーが front 語彙表（glossary.js の REPORT_VOCAB）にありません: "
        f"{missing}。語彙表へ追加してください（章立て・呼称・用語解説はそこから導出されます）"
    )
