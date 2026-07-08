"""σ水準線（horizontal_line）の共有線幅定数を固定する回帰テスト。

LEVEL_LINE_WIDTH の値（1px）と common からの公開（from common import ...）を
固定し、共有定数の存在・公開がリファクタで失われないことを保証する。
"""

from __future__ import annotations


def test_level_line_width_value_is_1() -> None:
    # Arrange / Act
    from common import LEVEL_LINE_WIDTH

    # Assert
    assert LEVEL_LINE_WIDTH == 1


def test_level_line_width_is_importable_from_common() -> None:
    # Arrange / Act
    import common

    # Assert
    assert hasattr(common, "LEVEL_LINE_WIDTH")
    assert "LEVEL_LINE_WIDTH" in common.__all__
