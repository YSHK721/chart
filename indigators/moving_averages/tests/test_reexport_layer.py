"""再エクスポート層（``indigators/moving_averages/__init__.py``）の検定（ISSUE-331）。

既存テストは全て ``from src import ...`` で src を直接読むため、パッケージ層を一度も
通らなかった。その間に src へ追加された公開名（stateful LWMA 等 3 名）が層で欠け、
本番 import 経路（profit_osi_ma / profit_arctan の ``from moving_averages import ...``）
だけが壊れる状態が 1 か月検出されなかった。本ファイルは**層そのもの**を対象にする。

固定する不変条件は「束縛と ``__all__`` の出所が ``src.__all__`` 単一であること」——
名前の列挙を期待値に書き写すと、それ自体が第 2 の手書きリストになる（同じ欠陥の再生産）。

読み込みは ``common.module_loader.load_package``（一意名 exec・T8: sys.path 非改変）。
``__init__.py`` の相対 import（``from .src import *``）ごと実行されるため、層の挙動は
本番 import 経路と同一である。
"""

from __future__ import annotations

from pathlib import Path

from common.module_loader import load_package

_pkg = load_package(
    "_moving_averages_pkg_layer", Path(__file__).resolve().parents[1]
)
_src = _pkg.src


def test_every_public_name_is_bound_on_the_package():
    """``from moving_averages import *`` が全名で成立する（欠けた名前を列挙して報告）。"""
    missing = [n for n in _pkg.__all__ if not hasattr(_pkg, n)]
    assert missing == []


def test_all_is_the_single_source_from_src():
    """``__all__`` は src.__all__ と同一物（写しではなく同じオブジェクトの再公開）。"""
    assert _pkg.__all__ is _src.__all__


def test_bound_objects_are_the_src_objects():
    """層は素通しであり、別実装・別名を挟まない（同名で src の実体と一致する）。"""
    mismatched = [
        n for n in _pkg.__all__
        if getattr(_pkg, n) is not getattr(_src, n)
    ]
    assert mismatched == []
