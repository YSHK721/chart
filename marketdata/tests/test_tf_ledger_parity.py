"""時間足台帳の JS 生成物が現在の Python 台帳と一致することの検定（ISSUE-254）。

方向: Python（唯一の定義）→ fixture / JS 生成物。台帳を変えたのに
``tools/gen_js_parity_golden.py`` を再実行し忘れると、JS だけ古い規則で動き続ける
（ISSUE-253 と同型の「静かなずれ」）。本検定はその再生成漏れを落とす。

JS 側の検定（``py_parity_golden.test.js``）は逆方向（fixture → JS の手編集）を見る。
両方向そろって初めて「定義は 1 つ」が構造的に保証される。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "indigators" / "market_profile" / "web" / "tests" / "fixtures" / "py_parity_golden.json"
JS_LEDGER = ROOT / "indigators" / "market_profile" / "web" / "js" / "domain" / "tf_ledger_generated.js"

_ENTRY = re.compile(
    r"\{ code: '([^']+)', barSec: (\d+), floorable: (true|false), calendar: (true|false) \}"
)


def _expected() -> "list[dict]":
    from marketdata import resample, tf_meta

    return [
        {
            "code": code,
            "barSec": int(tf_meta.TF_BAR_SEC[code]),
            "floorable": bool(d.floorable),
            "calendar": bool(d.calendar),
        }
        for code, d in resample.TF_DESCRIPTORS.items()
    ]


def _parse_js() -> "list[dict]":
    return [
        {"code": c, "barSec": int(s), "floorable": f == "true", "calendar": k == "true"}
        for c, s, f, k in _ENTRY.findall(JS_LEDGER.read_text(encoding="utf-8"))
    ]


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture 未生成")
def test_fixture_matches_the_python_ledger():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert golden.get("tf_ledger") == _expected(), (
        "台帳を変更したら tools/gen_js_parity_golden.py を再実行すること"
    )


@pytest.mark.skipif(not JS_LEDGER.exists(), reason="JS 生成物 未生成")
def test_generated_js_matches_the_python_ledger():
    assert _parse_js() == _expected(), (
        "台帳を変更したら tools/gen_js_parity_golden.py を再実行すること"
    )


def test_generated_js_is_marked_as_generated():
    """生成物であることがファイル冒頭で明示されている（手編集を誘発しない）。"""
    head = JS_LEDGER.read_text(encoding="utf-8").splitlines()[0]
    assert "自動生成" in head and "編集しない" in head
