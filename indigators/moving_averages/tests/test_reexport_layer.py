"""再エクスポート層（``indigators/moving_averages/__init__.py``）の検定（ISSUE-331）。

既存テストは全て ``from src import ...`` で src を直接読むため、パッケージ層を一度も
通らなかった。その間に src へ追加された公開名（stateful LWMA 等 3 名）が層で欠け、
本番 import 経路（profit_osi_ma / profit_arctan の ``from moving_averages import ...``）
だけが壊れる状態が 1 か月検出されなかった。本ファイルは**層そのもの**を対象にする。

固定する不変条件は「束縛と ``__all__`` の出所が ``src.__all__`` 単一であること」——
名前の列挙を期待値に書き写すと、それ自体が第 2 の手書きリストになる（同じ欠陥の再生産）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# パッケージ層を通すため indigators/ を追加する（src 直読みの既存テストとは逆の向き）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import moving_averages  # noqa: E402
from moving_averages import src as _src  # noqa: E402


def test_every_public_name_is_bound_on_the_package():
    """``from moving_averages import *`` が全名で成立する（欠けた名前を列挙して報告）。"""
    missing = [n for n in moving_averages.__all__ if not hasattr(moving_averages, n)]
    assert missing == []


def test_all_is_the_single_source_from_src():
    """``__all__`` は src.__all__ と同一物（写しではなく同じオブジェクトの再公開）。"""
    assert moving_averages.__all__ is _src.__all__


def test_bound_objects_are_the_src_objects():
    """層は素通しであり、別実装・別名を挟まない（同名で src の実体と一致する）。"""
    mismatched = [
        n for n in moving_averages.__all__
        if getattr(moving_averages, n) is not getattr(_src, n)
    ]
    assert mismatched == []
