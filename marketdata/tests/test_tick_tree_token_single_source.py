"""ref → ティック木トークンの写像が **台帳 1 箇所にしか無い** ことを走査で強制する（ISSUE-512 段階 1）。

宣言ではなく検定で強制する理由は、木のレイアウト権威を走査で固定している既存検定
（marketdata/tests/test_tick_tree_layout_authority.py）と同じ。かつて木の「形」は権威化
されたが、木の「枝名を誰が決めるか」は権威が無いまま残り、読取側が各自で埋めていた:

  - 市場プロファイルの dwell 側が ref → 枝名の手書き写像を 1 要素だけ持っていた
  - 形成中バー側の tick 読取 3 箇所が枝名を渡さず、木の側の既定引数
    （marketdata/tick_tree.py:30）に依存していた

後者が特に危険である。指紋（キャッシュ鍵）だけ token を渡して実データ読取に渡し忘れると、
**指紋は片方の木を見て実データはもう片方の木から読む**状態になり、値は正しいまま毎回読み直す
（ISSUE-450 と同型の「作ってから捨てる」欠陥）。出力検証では原理的に落ちない。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from marketdata.dataset_registry import REGISTRY

_ROOT = Path(__file__).resolve().parents[2]

#: ティック読取側（トークンを**使う**側。決める側になってはならない）。
_DWELL = (
    _ROOT / "indigators" / "market_profile" / "api" / "market_profile_api"
    / "compute" / "market_profile_dwell.py"
)
_FORMING_BAR = (
    _ROOT / "indigators" / "indicator_ui" / "api" / "adapter" / "compute" / "forming_bar.py"
)

#: ティック木を実際に読む関数。呼ぶなら **どの木か** を必ず名指しする。
#: ISSUE-512 段階 2: 読み元の列挙（`marketdata/tick_day_source.py` の day_tick_files）も
#: 木を名指しする読取である。
_TICK_READERS = frozenset({"day_parquet_files", "day_tick_files", "forming_bar_from_ticks"})


def test_the_dwell_reader_does_not_name_any_dataset_ref():
    """dwell 側に ref→token の手書き表が無い（ref の語彙は台帳が持つ）。

    落ちた場合の直し方: 手書きの写像を撤去し、窓口（marketdata/tf_meta.py）への委譲へ置換する。
    """
    # Arrange
    src = _DWELL.read_text(encoding="utf-8")

    # Act
    named = sorted(
        ref for ref in REGISTRY if re.search(rf"""["']{re.escape(ref)}["']""", src)
    )

    # Assert
    assert not named, (
        f"{_DWELL.name} が datasetRef を名指ししています: {named}。"
        " ref→トークンの写像は marketdata.dataset_registry の台帳が唯一源であり、"
        " 読取側は marketdata.tf_meta.tick_tree_token 経由で受け取る。"
    )


@pytest.mark.parametrize(
    "path",
    [_FORMING_BAR],
    ids=["forming_bar"],
)
def test_every_tick_read_names_the_tree_it_reads(path):
    """ティック読取の呼び出しは **すべて** ``symbol=`` で木を名指しする（既定引数に頼らない）。

    1 箇所でも欠けると、その経路だけが木の側の既定引数（marketdata/tick_tree.py:30）の枝を読む。
    """
    # Arrange
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Act
    unnamed = [
        (node.func.id, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _TICK_READERS
        and not any(kw.arg == "symbol" for kw in node.keywords)
    ]

    # Assert
    assert not unnamed, (
        f"{path.name} に木を名指ししないティック読取があります: {unnamed}。"
        " symbol=tick_tree_token(ref) を渡してください（1 箇所だけ直すと"
        " 指紋と実データが別の木を見る状態になります）。"
    )
