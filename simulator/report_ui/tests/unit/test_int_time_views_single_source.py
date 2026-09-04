"""int 時刻ビューが単一ソースであることの構造検定（H-D4）。

「写さない」は宣言では守れない（本リポジトリで繰り返し起きている壊れ方）。ソーステキストを
読んで、旧 4 定義がどちらの利用側にも**存在しない**ことと、両者が `int_time_views` を
import していることを機械的に固定する。

背景（実測 2026-08-11・`python -m tools.codescan`）:
    43 行 [type-2/block] 2 箇所
      simulator/report_ui/tools/export_report_payload.py:69-111
      simulator/sim_ui/adapter/report_payload_writer.py:52-94
    → リポジトリ全体の clone 第 1 位。同じ payload を作る 2 経路なので、食い違っても
      例外は出ず report.json が静かにずれる。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
_SOURCE = _REPO / "simulator" / "report_ui" / "tools" / "int_time_views.py"
_CONSUMERS = {
    "export_report_payload": _REPO / "simulator" / "report_ui" / "tools" / "export_report_payload.py",
    "report_payload_writer": _REPO / "simulator" / "sim_ui" / "adapter" / "report_payload_writer.py",
}
#: 単一ソースが所有する定義（利用側が再定義してはならない名前）。
_OWNED = ("unix_seconds", "IntTimeBar", "IntTimeTrade", "ResultView")
#: 移設前に利用側が持っていた private 名（残っていれば移設の取り残し）。
_OLD_NAMES = ("_unix", "_IntTimeBar", "_IntTimeTrade", "_ResultView")


def _defines(source: str, name: str) -> bool:
    """`name` を def / class として定義しているか（import の as 束縛は定義ではない）。"""
    return re.search(rf"^\s*(?:def|class)\s+{re.escape(name)}\b", source, re.MULTILINE) is not None


def test_単一ソースが4定義を持つ() -> None:
    text = _SOURCE.read_text(encoding="utf-8")
    assert [n for n in _OWNED if _defines(text, n)] == list(_OWNED)


@pytest.mark.parametrize("name", sorted(_CONSUMERS))
def test_利用側は旧4定義を持たない(name: str) -> None:
    text = _CONSUMERS[name].read_text(encoding="utf-8")
    offenders = [n for n in (*_OWNED, *_OLD_NAMES) if _defines(text, n)]
    assert offenders == [], f"{name} に移設の取り残しがあります: {offenders}"


@pytest.mark.parametrize("name", sorted(_CONSUMERS))
def test_利用側は単一ソースをimportする(name: str) -> None:
    text = _CONSUMERS[name].read_text(encoding="utf-8")
    assert "int_time_views" in text, f"{name} が単一ソースを import していません"


@pytest.mark.parametrize("name", sorted(_CONSUMERS))
def test_利用側からpandasのTimestamp直呼びが時刻正規化に残っていない(name: str) -> None:
    """時刻の int 化を利用側で書き直していないこと（別実装の再発）。"""
    text = _CONSUMERS[name].read_text(encoding="utf-8")
    assert "pd.Timestamp(t).timestamp()" not in text
