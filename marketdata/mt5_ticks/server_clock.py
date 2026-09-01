"""サーバ時刻ラベル → UTC（domain A・**依存ゼロ**・stdlib のみ）。

MT5 が返す ``time_msc`` はブローカー（OANDA-Japan MT5）の**サーバ時刻ラベル**であり UTC では
ない。ISSUE-447 T1 の当初結論「サーバ時刻 = UTC+3」は**夏時間の値**であって、固定 +3h 補正は
冬季に 1 時間ずれる（T1 の訂正）。本モジュールは EET/EEST（冬 UTC+2 / 夏 UTC+3）の DST 規則を
**算術で**適用する唯一の場所である。

実測の固定点（ISSUE-447 T1b・全 76 か月アーカイブの各月先頭行）:
    2020-12 / 2021-01 / 2026-01 = ``17:00`` ラベル ＝ 冬 UTC+2、
    2021-04 / 2026-08 = ``18:00`` ラベル ＝ 夏 UTC+3。
    ``marketdata/tests/test_mt5_server_clock.py`` が固定点として保持する。

未検証（仮説・V-3 で確定）:
    DST 規則そのもの（3 月最終日曜〜10 月最終日曜が夏）は EU の暦規則であり、ブローカーが
    これに厳密に従うかは**実端末で未検証**である。V-3（切替日 24h の単調性検査）まで
    「仮説」として扱う。zoneinfo/tzdata の実在も未検証のため（V-6）、既定は算術規則とする。

逆変換を持たない理由:
    UTC → ラベルは 10 月切替日の 1 時間が多価（同じラベルが夏・冬の 2 回出現）になるため
    関数として定義できない。**実装しない**（設計 §4）。同じ理由で、ラベル 03:00〜04:00 台の
    10 月最終日曜は本モジュールでも一意に決められず、夏側へ倒す（記録のみ・緩和しない）。
"""
from __future__ import annotations

import datetime as dt

#: 冬（EET）のオフセット秒。
WINTER_OFFSET_SECONDS = 7200
#: 夏（EEST）のオフセット秒。
SUMMER_OFFSET_SECONDS = 10800

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)

#: 夏時間の開始・終了を判定するラベル上の時刻。
#:
#: EU 規則の切替は 01:00Z に起きる。ラベル（= UTC+offset）で見ると 3 月は 03:00 が 04:00 へ飛び、
#: 10 月は 04:00 が 03:00 へ戻る。よってラベル空間の判定境界は 3 月 03:00 / 10 月 04:00 になる。
_MARCH_SWITCH_HOUR = 3
_OCTOBER_SWITCH_HOUR = 4


def _label_datetime(server_label_ms: int) -> dt.datetime:
    """ラベル ms を「壁時計の読み」として datetime へ（整数 timedelta ＝ 丸め誤差なし）。"""
    return _EPOCH + dt.timedelta(milliseconds=int(server_label_ms))


def _last_sunday(year: int, month: int) -> dt.date:
    """``year`` 年 ``month`` 月の最終日曜。"""
    if month == 12:
        last = dt.date(year, 12, 31)
    else:
        last = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - 6) % 7)


def _summer_window(year: int) -> "tuple[dt.datetime, dt.datetime]":
    """``year`` の夏時間区間 ``[開始, 終了)`` をラベル空間の datetime で返す。"""
    start = dt.datetime.combine(
        _last_sunday(year, 3), dt.time(_MARCH_SWITCH_HOUR), tzinfo=dt.timezone.utc
    )
    end = dt.datetime.combine(
        _last_sunday(year, 10), dt.time(_OCTOBER_SWITCH_HOUR), tzinfo=dt.timezone.utc
    )
    return start, end


def offset_seconds(server_label_ms: int) -> int:
    """ラベルに適用すべきオフセット秒を返す（``7200`` = 冬 / ``10800`` = 夏の 2 値のみ）。"""
    label = _label_datetime(server_label_ms)
    start, end = _summer_window(label.year)
    if start <= label < end:
        return SUMMER_OFFSET_SECONDS
    return WINTER_OFFSET_SECONDS


def to_utc_ms(server_label_ms: int) -> int:
    """サーバ時刻ラベル ms を UTC epoch ms へ変換する（``label - offset*1000``）。"""
    label_ms = int(server_label_ms)
    return label_ms - offset_seconds(label_ms) * 1000


def utc_day_of(server_label_ms: int) -> dt.date:
    """ラベルが属する **UTC 日**（tick 木の日 partition を決める唯一の判断）。

    ラベルの日付ではなく変換後 UTC の日付である点が要点で、ここを取り違えると日 partition が
    まるごと 1 日ずれる。
    """
    return (_EPOCH + dt.timedelta(milliseconds=to_utc_ms(server_label_ms))).date()


def is_dst_transition_day(day: dt.date) -> bool:
    """``day``（UTC 日）が DST 切替日（3 月/10 月の最終日曜）か。**記録のみ**。

    切替日であることを理由に検証（単調性・境界一致）を緩和してはならない。緩和すると
    「切替日だから」で本物の欠陥を通す。用途は運用ログへの記録と、多価区間を扱う
    検定の明示的な除外だけである。
    """
    d = day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day))
    return d in (_last_sunday(d.year, 3), _last_sunday(d.year, 10))
