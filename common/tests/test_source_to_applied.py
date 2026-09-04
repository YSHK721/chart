"""common.applied_price.SOURCE_TO_APPLIED — UI ソース値 → 適用価格種別の写像を固定する。

ISSUE-179 項目 4 で ``moving_averages/src/lwc_chart.py`` ↔ ``btlm_trail/src/core.py`` ↔
``ma_marod/src/core.py`` に 3 重複製されていた 8 択写像を共有プリミティブへ 1 本化した。
本テストは移設元と同一の 8 キー・同一の対応値を固定する（写像そのものが計算の原子）。
"""

from __future__ import annotations


def test_source_to_applied_has_exactly_eight_ui_source_keys() -> None:
    # Arrange / Act
    from common.applied_price import SOURCE_TO_APPLIED

    # Assert: catalog の source enum と同一の 8 択。
    assert set(SOURCE_TO_APPLIED) == {
        "close", "open", "high", "low", "hl2", "hlc3", "hlcc4", "ohlc4",
    }


def test_source_to_applied_maps_each_key_to_expected_kind() -> None:
    # Arrange
    from common.applied_price import AppliedPrice, SOURCE_TO_APPLIED

    # Act / Assert: 合成価格の対応（hl2=MEDIAN / hlc3=TYPICAL / hlcc4=WEIGHTED / ohlc4=OHLC4）。
    assert SOURCE_TO_APPLIED["close"] is AppliedPrice.CLOSE
    assert SOURCE_TO_APPLIED["open"] is AppliedPrice.OPEN
    assert SOURCE_TO_APPLIED["high"] is AppliedPrice.HIGH
    assert SOURCE_TO_APPLIED["low"] is AppliedPrice.LOW
    assert SOURCE_TO_APPLIED["hl2"] is AppliedPrice.MEDIAN
    assert SOURCE_TO_APPLIED["hlc3"] is AppliedPrice.TYPICAL
    assert SOURCE_TO_APPLIED["hlcc4"] is AppliedPrice.WEIGHTED
    assert SOURCE_TO_APPLIED["ohlc4"] is AppliedPrice.OHLC4


def test_source_to_applied_is_exposed_from_package_surface() -> None:
    # Arrange / Act
    import common

    # Assert
    assert hasattr(common, "SOURCE_TO_APPLIED")
    assert "SOURCE_TO_APPLIED" in common.__all__


def test_source_keys_are_lowercase_only() -> None:
    # Arrange
    from common.applied_price import SOURCE_TO_APPLIED

    # Assert: 呼び出し側は ``str(source).lower()`` で引くため、キーは小文字のみ。
    assert all(k == k.lower() for k in SOURCE_TO_APPLIED)
