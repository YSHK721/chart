"""σ水準線（horizontal_line）の共有線幅定数を固定する回帰テスト。

LEVEL_LINE_WIDTH の値（2px・視認性向上の意図値＝コミット 5dabbdb で 1→2）と common_view からの
公開を固定し、共有定数の存在・公開がリファクタで失われないことを保証する（ISSUE-093: 定数変更時の
テスト更新漏れを是正。線幅を変える場合は本テストも同時更新）。表示定数の唯一の公開元は common_view
（ISSUE-104 🟡-1 で common からの後方互換再エクスポートを撤去＝安定度逆転の解消）。
"""

from __future__ import annotations


def test_level_line_width_value_is_2() -> None:
    # Arrange / Act
    from common_view import LEVEL_LINE_WIDTH

    # Assert: 系列線（幅1）と判別できる 2px（common_view/level_style.py の意図値と一致）。
    assert LEVEL_LINE_WIDTH == 2


def test_level_line_width_is_importable_from_common_view() -> None:
    # Arrange / Act
    import common_view

    # Assert: 表示定数の唯一の公開元は common_view。
    assert hasattr(common_view, "LEVEL_LINE_WIDTH")
    assert "LEVEL_LINE_WIDTH" in common_view.__all__


def test_level_line_width_is_not_re_exported_from_common() -> None:
    # Arrange / Act: 計算・安定層 common は表示定数を再エクスポートしない（安定度逆転の防止）。
    import common

    # Assert: ISSUE-104 🟡-1 の是正（common→common_view 逆依存の撤去）を固定する。
    assert "LEVEL_LINE_WIDTH" not in common.__all__
    assert "level_colors" not in common.__all__
