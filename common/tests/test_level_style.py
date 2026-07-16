"""σ水準線（horizontal_line）の共有線幅定数を固定する回帰テスト。

LEVEL_LINE_WIDTH の値（2px・視認性向上の意図値＝コミット 5dabbdb で 1→2）と common からの
公開（from common import ...）を固定し、共有定数の存在・公開がリファクタで失われないことを
保証する（ISSUE-093: 定数変更時のテスト更新漏れを是正。線幅を変える場合は本テストも同時更新）。
"""

from __future__ import annotations


def test_level_line_width_value_is_2() -> None:
    # Arrange / Act
    from common import LEVEL_LINE_WIDTH

    # Assert: 系列線（幅1）と判別できる 2px（common/level_style.py の意図値と一致）。
    assert LEVEL_LINE_WIDTH == 2


def test_level_line_width_is_importable_from_common() -> None:
    # Arrange / Act
    import common

    # Assert
    assert hasattr(common, "LEVEL_LINE_WIDTH")
    assert "LEVEL_LINE_WIDTH" in common.__all__
