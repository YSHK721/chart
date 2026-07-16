"""common_view — 表示系（level_colors・LEVEL_LINE_WIDTH）の公開面と値を固定する回帰テスト。

ISSUE-092 ⑥（common の計算/表示分割）で common から common_view へ分離した表示系プリミティブ
について、(1) 公開シンボルの存在・__all__ 登録、(2) 値の固定（LEVEL_LINE_WIDTH==2、
level_colors の緑→赤写像）を保証する。分割リファクタで公開面・値が失われないことを固定する。
"""

from __future__ import annotations

import numpy as np


def test_level_line_width_value_is_2() -> None:
    # Arrange / Act
    from common_view import LEVEL_LINE_WIDTH

    # Assert: 系列線（幅1）と判別できる 2px（common_view/level_style.py の意図値と一致）。
    assert LEVEL_LINE_WIDTH == 2


def test_public_surface_is_exposed_from_common_view() -> None:
    # Arrange / Act
    import common_view

    # Assert: level_colors・LEVEL_LINE_WIDTH が公開面（__all__）に登録されている。
    assert hasattr(common_view, "level_colors")
    assert hasattr(common_view, "LEVEL_LINE_WIDTH")
    assert "level_colors" in common_view.__all__
    assert "LEVEL_LINE_WIDTH" in common_view.__all__


def test_level_colors_maps_extremes_red_center_green() -> None:
    # Arrange
    from common_view import level_colors

    values = np.array([-3.0, 0.0, 3.0])

    # Act
    colors = level_colors(values)

    # Assert: 両極端=赤(#d32f2f), 中心=緑(#2e7d32)。
    assert colors == ["#d32f2f", "#2e7d32", "#d32f2f"]
