"""壊れた末尾で**検知器自身が落ちない**ことの検定（ISSUE-526 段 2・同型 ISSUE-529）。

段 1 の検知器は末尾 1 行を読んで先端を決める。M1 の末尾は非原子的な追記の途中で torn に
なりうる（NUL の混入・列数の崩れ・途中で切れた行）。そこで例外が素通りすると、
**供給の停止を検知するはずのものが、供給の停止と同じ理由で死ぬ**——ISSUE-529 と同じ形である。

固定するもの:
    1. 壊れた末尾で :func:`marketdata.supply_freshness.m1_tip` は落ちず、
       健全とも「まだ 1 行も無い」とも別の**名前付きの値**を返す。
    2. 判定もその名前付きの値になる。**黙って健全にしない**。
    3. 壊れた系列は、兄弟系列の基準としても使われない（壊れた先端で相対比較しない）。
    4. 末尾が直れば、また判定できるようになる（壊れた観測を引きずらない）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd

from marketdata import supply_freshness

_HEADER = "date,open,high,low,close,volume,up,dn"

#: 健全な行の並び（末尾だけを壊すため、手前は常に正しい形にする）。
_GOOD_ROWS = "\n".join(
    f"2026-09-24 22:2{minute}:00,1,2,0.5,1.5,10,6,4" for minute in range(3))

#: 壊れた末尾の 3 形。NUL 混入は ISSUE-529 の実物の形である。
_NUL_TAIL = "\0\0026-09-24 22:30:00,1,2,0.5,1.5,10,6,4\n"
_TOO_MANY_FIELDS_TAIL = "2026-09-24 22:30:00,1,2,0.5,1.5,10,6,4,9,9\n"
_NOT_A_DATE_TAIL = "torn,1,2,0.5,1.5,10,6,4\n"


def _write_m1(data_dir, series: str, *, last_minute: str, rows: int = 3) -> None:
    """健全な M1 を rows 行だけ書く（末尾が last_minute になる）。"""
    end = pd.Timestamp(last_minute)
    index = pd.date_range(end=end, periods=rows, freq="min")
    lines = [_HEADER] + [
        f"{ts:%Y-%m-%d %H:%M:%S},1,2,0.5,1.5,10,6,4" for ts in index
    ]
    (data_dir / f"{series}_m1.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_torn_m1(data_dir, series: str, torn_tail: str) -> None:
    """健全な行のあとに壊れた 1 行を足した M1 を書く（追記途中の再現）。"""
    (data_dir / f"{series}_m1.csv").write_bytes(
        (_HEADER + "\n" + _GOOD_ROWS + "\n" + torn_tail).encode("utf-8"))


# =====================================================================
# 1. 先端の読み口は落ちず、名前付きの値を返す
# =====================================================================

def test_the_tip_is_unreadable_when_the_last_row_starts_with_nul_bytes(tmp_path):
    """NUL で始まる末尾（ISSUE-529 の実物の形）でも落ちず、先端は読めないと言う。"""
    # Arrange
    _write_torn_m1(tmp_path, "jp225_mt5", _NUL_TAIL)

    # Act
    tip = supply_freshness.m1_tip("jp225_mt5", data_dir=tmp_path)

    # Assert
    assert tip == supply_freshness.UNREADABLE


def test_the_tip_is_unreadable_when_the_last_row_has_too_many_fields(tmp_path):
    """列数が崩れた末尾でも落ちず、先端は読めないと言う。"""
    # Arrange
    _write_torn_m1(tmp_path, "jp225_mt5", _TOO_MANY_FIELDS_TAIL)

    # Act
    tip = supply_freshness.m1_tip("jp225_mt5", data_dir=tmp_path)

    # Assert
    assert tip == supply_freshness.UNREADABLE


def test_the_tip_is_unreadable_when_the_last_row_has_no_date(tmp_path):
    """日付として読めない末尾でも落ちず、先端は読めないと言う。"""
    # Arrange
    _write_torn_m1(tmp_path, "jp225_mt5", _NOT_A_DATE_TAIL)

    # Act
    tip = supply_freshness.m1_tip("jp225_mt5", data_dir=tmp_path)

    # Assert
    assert tip == supply_freshness.UNREADABLE


def test_a_torn_tail_is_told_apart_from_a_series_that_has_no_file_yet(tmp_path):
    """壊れた末尾と「まだ 1 行も無い」を同じ値にしない（黙って一緒にしない）。"""
    # Arrange
    _write_torn_m1(tmp_path, "jp225_mt5", _NUL_TAIL)

    # Act
    torn = supply_freshness.m1_tip("jp225_mt5", data_dir=tmp_path)
    missing = supply_freshness.m1_tip("jp225_mt5_spread", data_dir=tmp_path)

    # Assert
    assert (torn, missing) == (supply_freshness.UNREADABLE, None)


# =====================================================================
# 2. 判定も名前付きの値になる（健全にしない）
# =====================================================================

def test_the_verdict_of_a_torn_series_is_unreadable_and_not_healthy(tmp_path):
    """壊れた末尾の系列は、健全とも異常とも別の値で報される。"""
    # Arrange
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    _write_torn_m1(tmp_path, "jp225_mt5_spread", _NUL_TAIL)
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"), data_dir=tmp_path)
    watch.observe()

    # Act
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:31:00")
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        "jp225_mt5": supply_freshness.UNDECIDABLE,
        "jp225_mt5_spread": supply_freshness.UNREADABLE,
    }


def test_the_unreadable_verdict_is_a_value_of_its_own(tmp_path):
    """先端が読めないは、健全・異常・判定不能のいずれとも別の値である。"""
    # Arrange / Act / Assert
    assert len({
        supply_freshness.HEALTHY,
        supply_freshness.STALE,
        supply_freshness.UNDECIDABLE,
        supply_freshness.UNREADABLE,
    }) == 4


def test_the_watch_keeps_observing_after_a_torn_tail(tmp_path):
    """壊れた末尾を 2 回続けて観測しても落ちない（前回観測へ壊れた値を残さない）。"""
    # Arrange
    _write_torn_m1(tmp_path, "jp225_mt5", _NUL_TAIL)
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"), data_dir=tmp_path)
    watch.observe()

    # Act
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:31:00")
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        "jp225_mt5": supply_freshness.UNREADABLE,
        "jp225_mt5_spread": supply_freshness.UNDECIDABLE,
    }


# =====================================================================
# 3. 壊れた系列は基準にしない／直れば戻る
# =====================================================================

def test_a_torn_series_is_not_used_as_a_reference_for_its_sibling(tmp_path):
    """壊れた先端で相対比較しない（止まっている兄弟を異常と断じない）。"""
    # Arrange
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"), data_dir=tmp_path)
    watch.observe()

    # Act: 基準側が 5 分進んだが、その末尾は壊れている。対象側は 1 分も進んでいない。
    _write_torn_m1(tmp_path, "jp225_mt5", _NUL_TAIL)
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        "jp225_mt5": supply_freshness.UNREADABLE,
        "jp225_mt5_spread": supply_freshness.UNDECIDABLE,
    }


def test_a_series_can_be_judged_again_once_its_tail_is_repaired(tmp_path):
    """末尾が直れば、また兄弟どうしの相対比較で判定できる。"""
    # Arrange
    _write_torn_m1(tmp_path, "jp225_mt5", _NUL_TAIL)
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:26:00")
    watch = supply_freshness.M1FreshnessWatch(
        ("jp225_mt5", "jp225_mt5_spread"), data_dir=tmp_path)
    watch.observe()
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:26:00")
    watch.observe()

    # Act
    _write_m1(tmp_path, "jp225_mt5", last_minute="2026-09-24 22:31:00")
    _write_m1(tmp_path, "jp225_mt5_spread", last_minute="2026-09-24 22:31:00")
    verdicts = watch.observe()

    # Assert
    assert verdicts == {
        "jp225_mt5": supply_freshness.HEALTHY,
        "jp225_mt5_spread": supply_freshness.HEALTHY,
    }
