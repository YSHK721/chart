"""MT5 サーバ時刻ラベル → UTC 変換の検定（ISSUE-447 段階 1 / 検定 N-1・B-3）。

MT5 が返す ``time_msc`` は **ブローカーのサーバ時刻ラベル**であり UTC ではない
（ISSUE-447 T1 の訂正: 固定 +3h 補正は冬季に 1 時間ずれる）。本検定は
``marketdata.mt5_ticks.server_clock`` が EET/EEST（冬 UTC+2 / 夏 UTC+3）の
DST 規則で変換することを、**実測の固定点**（ISSUE-447 T1b・全 76 か月アーカイブの
各月先頭行）で固定する。

固定点の出所（捏造しない）: OANDA 月別アーカイブ ``ticks_JP225_<YYYY-MM>.zip`` の先頭行は
その月の初日 JST 00:00（= UTC 前日 15:00）に対応する。先頭行のサーバ時刻ラベルが
``17:00`` なら +2h、``18:00`` なら +3h である。
"""
from __future__ import annotations

import datetime as dt

import pytest

from marketdata.mt5_ticks import server_clock


def _ms(y, mo, d, h, mi=0, s=0) -> int:
    """壁時計の読みを epoch ms へ（ラベルも UTC も同じ関数で作る＝差だけが意味を持つ）。"""
    return int(dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc).timestamp() * 1000)


# ISSUE-447 T1b の実測固定点: (ラベル, 期待 UTC, 期待オフセット秒, 出所)
_T1B_FIXED_POINTS = [
    (_ms(2020, 11, 30, 17), _ms(2020, 11, 30, 15), 7200, "2020-12 先頭行 11.30 17:00 = 冬"),
    (_ms(2020, 12, 31, 17), _ms(2020, 12, 31, 15), 7200, "2021-01 先頭行 12.31 17:00 = 冬"),
    (_ms(2025, 12, 31, 17), _ms(2025, 12, 31, 15), 7200, "2026-01 先頭行 12.31 17:00 = 冬"),
    (_ms(2021, 3, 31, 18), _ms(2021, 3, 31, 15), 10800, "2021-04 先頭行 03.31 18:00 = 夏"),
    (_ms(2026, 7, 31, 18), _ms(2026, 7, 31, 15), 10800, "2026-08 先頭行 07.31 18:00 = 夏"),
]


# =====================================================================
# N-1 正常系: 実測固定点への一致
# =====================================================================

@pytest.mark.parametrize(
    "label_ms,expected_utc_ms,expected_offset,origin",
    _T1B_FIXED_POINTS,
    ids=[p[3] for p in _T1B_FIXED_POINTS],
)
def test_to_utc_ms_matches_the_measured_t1b_fixed_points(
    label_ms, expected_utc_ms, expected_offset, origin
):
    """N-1: 冬 3 点 = +2h・夏 2 点 = +3h（ISSUE-447 T1b 実測）に一致する。"""
    # Arrange: 固定点は上表（実測）。
    # Act
    got_offset = server_clock.offset_seconds(label_ms)
    got_utc = server_clock.to_utc_ms(label_ms)
    # Assert
    assert got_offset == expected_offset, origin
    assert got_utc == expected_utc_ms, origin


def test_offset_seconds_is_always_one_of_the_two_broker_offsets():
    """N-1: オフセットは 7200 / 10800 の 2 値しか取らない（第 3 の値を作らない）。"""
    # Arrange: 1 年を 6 時間刻みで走査する。
    start = _ms(2026, 1, 1, 0)
    step = 6 * 3600 * 1000
    # Act
    got = {server_clock.offset_seconds(start + i * step) for i in range(4 * 365)}
    # Assert
    assert got == {7200, 10800}


def test_to_utc_ms_is_the_label_minus_the_offset():
    """N-1: 変換式が ``label - offset*1000`` であること（別式を持ち込まない）。"""
    label = _ms(2026, 8, 25, 12)
    assert server_clock.to_utc_ms(label) == label - server_clock.offset_seconds(label) * 1000


def test_the_inverse_conversion_is_not_implemented():
    """UTC→ラベルは多価のため**実装しない**（設計 §4）。存在したら仕様逸脱。"""
    assert not hasattr(server_clock, "to_server_label_ms")
    assert not hasattr(server_clock, "from_utc_ms")


# =====================================================================
# B-3 境界: DST 切替日
# =====================================================================

@pytest.mark.parametrize(
    "day,expected",
    [
        (dt.date(2021, 3, 28), True),    # 3 月最終日曜
        (dt.date(2021, 10, 31), True),   # 10 月最終日曜
        (dt.date(2026, 3, 29), True),
        (dt.date(2026, 10, 25), True),
        (dt.date(2026, 3, 22), False),   # 1 週間前の日曜は切替日ではない
        (dt.date(2026, 8, 25), False),
        (dt.date(2026, 10, 26), False),  # 切替日の翌日
    ],
)
def test_is_dst_transition_day_marks_only_the_last_sundays(day, expected):
    """B-3: 3 月/10 月の最終日曜だけを切替日として**記録**する（緩和はしない）。"""
    assert server_clock.is_dst_transition_day(day) is expected


def test_utc_is_monotonic_across_the_march_transition():
    """B-3: 3 月切替（ラベルに存在しない 1 時間を挟む）を跨いで UTC が単調増加する。"""
    # Arrange: 2026-03-29 が最終日曜。ラベル 02:59 は冬、04:00 は夏（03:00 台は存在しない）。
    labels = [
        _ms(2026, 3, 29, 1),
        _ms(2026, 3, 29, 2, 59, 59),
        _ms(2026, 3, 29, 4),
        _ms(2026, 3, 29, 5),
    ]
    # Act
    utc = [server_clock.to_utc_ms(v) for v in labels]
    # Assert
    assert utc == sorted(utc)
    assert all(b > a for a, b in zip(utc, utc[1:]))
    # 切替の前後で 1 時間ぶんのオフセット差が生じている。
    assert server_clock.offset_seconds(labels[1]) == 7200
    assert server_clock.offset_seconds(labels[2]) == 10800


def test_utc_is_monotonic_across_the_october_transition_outside_the_ambiguous_hour():
    """B-3: 10 月切替を跨いで UTC が単調（**多価の 03:00 台は除く**）。

    10 月最終日曜のラベル 03:00〜04:00 は夏・冬の 2 回出現し、ラベル単独では
    区別できない（設計 §4 の「逆変換は多価」と同じ理由）。本検定は多価区間の外側で
    単調性を固定し、多価区間の扱いを暗黙に「解決済み」と主張しない。
    """
    labels = [
        _ms(2026, 10, 25, 1),
        _ms(2026, 10, 25, 2, 59, 59),
        _ms(2026, 10, 25, 4),
        _ms(2026, 10, 25, 5),
    ]
    utc = [server_clock.to_utc_ms(v) for v in labels]
    assert all(b > a for a, b in zip(utc, utc[1:]))
    assert server_clock.offset_seconds(labels[1]) == 10800
    assert server_clock.offset_seconds(labels[2]) == 7200


# =====================================================================
# utc_day_of: UTC 日 partition の唯一の決め手
# =====================================================================

def test_utc_day_of_uses_the_converted_utc_not_the_label():
    """ラベルの日付ではなく変換後 UTC の日付を返す（日 partition が 1 日ずれない）。"""
    # 夏（+3h）: ラベル 2026-08-01 02:59:59 → UTC 2026-07-31 23:59:59
    assert server_clock.utc_day_of(_ms(2026, 8, 1, 2, 59, 59)) == dt.date(2026, 7, 31)
    # 同 03:00:00 → UTC 2026-08-01 00:00:00
    assert server_clock.utc_day_of(_ms(2026, 8, 1, 3, 0, 0)) == dt.date(2026, 8, 1)
    # 冬（+2h）: ラベル 2026-01-01 01:59:59 → UTC 2025-12-31
    assert server_clock.utc_day_of(_ms(2026, 1, 1, 1, 59, 59)) == dt.date(2025, 12, 31)
    assert server_clock.utc_day_of(_ms(2026, 1, 1, 2, 0, 0)) == dt.date(2026, 1, 1)
