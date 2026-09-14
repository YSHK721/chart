"""§4.3 地平 3 段（短期＝すべて / 中期＝1h 以上 / 長期＝1D 以上）を固定する。

§5.5.5 の背景 3 分割は「§4.3 と同じ区分」であることが採用根拠（新しい概念を増やさない）。
よって地平の定義は本モジュール 1 箇所だけが持つ。
"""
from __future__ import annotations

import pytest

from dashboard_ui.domain.horizon import (
    TIMEFRAME_ORDER,
    Horizon,
    horizons_of,
    includes,
    timeframe_rank,
)


#: 地平の入れ子の並び（短い順）。§4.3 の表そのものであり、実装から作らない。
_NESTED_ORDER = (Horizon.SHORT, Horizon.MEDIUM, Horizon.LONG)


def test_the_timeframe_order_is_the_eight_timeframes_of_the_design() -> None:
    assert TIMEFRAME_ORDER == ("1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M")


def test_there_are_exactly_three_horizons() -> None:
    assert [h.name for h in Horizon] == ["SHORT", "MEDIUM", "LONG"]


class TestTimeframeRank:
    def test_rank_increases_with_the_timeframe_length(self) -> None:
        assert timeframe_rank("1m") < timeframe_rank("1h") < timeframe_rank("1M")

    def test_an_unknown_timeframe_is_rejected(self) -> None:
        """無言で 0 扱いにしない（未知の足を短期へ紛れ込ませない）。"""
        with pytest.raises(ValueError):
            timeframe_rank("3m")


class TestIncludes:
    @pytest.mark.parametrize("timeframe", TIMEFRAME_ORDER)
    def test_short_includes_every_timeframe(self, timeframe: str) -> None:
        assert includes(Horizon.SHORT, timeframe) is True

    @pytest.mark.parametrize(
        "timeframe,expected",
        [("15m", False), ("1h", True), ("4h", True), ("1M", True)],
    )
    def test_medium_starts_at_one_hour_inclusive(self, timeframe: str, expected: bool) -> None:
        """境界値: 「1h 以上」は 1h を含む。"""
        assert includes(Horizon.MEDIUM, timeframe) is expected

    @pytest.mark.parametrize(
        "timeframe,expected",
        [("4h", False), ("1D", True), ("1W", True), ("1M", True)],
    )
    def test_long_starts_at_one_day_inclusive(self, timeframe: str, expected: bool) -> None:
        """境界値: 「1D 以上」は 1D を含む。"""
        assert includes(Horizon.LONG, timeframe) is expected

    def test_an_unknown_timeframe_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            includes(Horizon.SHORT, "2h")


class TestHorizonsOf:
    def test_a_one_minute_level_belongs_to_the_short_horizon_only(self) -> None:
        assert horizons_of("1m") == (Horizon.SHORT,)

    def test_a_four_hour_level_belongs_to_short_and_medium(self) -> None:
        assert horizons_of("4h") == (Horizon.SHORT, Horizon.MEDIUM)

    def test_a_monthly_level_belongs_to_all_three(self) -> None:
        assert horizons_of("1M") == (Horizon.SHORT, Horizon.MEDIUM, Horizon.LONG)

    @pytest.mark.parametrize("timeframe", TIMEFRAME_ORDER)
    def test_the_horizons_are_nested(self, timeframe: str) -> None:
        """§4.3 の段は入れ子（長期 ⊂ 中期 ⊂ 短期）。段が交差すると「次のターゲット」が壊れる。

        入れ子であることは「その足が属する地平が、短い順の並びの**接頭辞**になる」と同値
        である（長期に入るなら中期にも短期にも入る＝途中を飛ばした組み合わせが現れない）。
        接頭辞の形で書くと分岐が要らない: 分岐で書くと入力によって実行経路が変わり、
        条件に入らない足では**何も検査しないまま緑になる**。
        """
        # Act
        marks = horizons_of(timeframe)

        # Assert
        assert marks == _NESTED_ORDER[: len(marks)]
        assert marks[0] is Horizon.SHORT
