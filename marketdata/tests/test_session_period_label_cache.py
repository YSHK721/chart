"""``session_period_label`` の記憶（ISSUE-450）が答えを変えないことを固定する。

背景（実測 2026-08-28）:
    上位足投影は「その時刻はどのバーに属するか」をチャート足 1 本ごとに問う。C=1m / H=1M の
    1 リクエストで ``session_period_label`` が 25,131 回呼ばれ 0.71 秒を占めていた。答えは
    ブローカー暦日ごとに一定なので暦日をキーに記憶する。

ここで固定する不変量は 2 つ:
    1. **ラベル日は日内時刻に依存しない**（暦日だけをキーにしてよい根拠そのもの）。
    2. 記憶の有無で答えが変わらない（規則源 ``resample.period_label_naive`` と一致し続ける）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from marketdata.resample import period_label_naive
from marketdata.session_day import (
    _broker_date,
    _period_label_of_broker_day,
    session_period_label,
)

# 境界を含む代表点（月末・月初・金曜・土曜＝ブローカー週の始まり・DST 切替日・閏日）。
_DAYS = [
    "2026-01-01", "2026-01-30", "2026-01-31", "2026-02-01",
    "2026-03-08", "2026-03-09",          # 米 DST 開始（3 月第 2 日曜）とその翌日
    "2026-11-01", "2026-11-02",          # 米 DST 終了（11 月第 1 日曜）とその翌日
    "2024-02-28", "2024-02-29", "2024-03-01",   # 閏日
    "2026-08-28", "2026-08-29", "2026-08-30",   # 金・土・日
    "2026-12-31", "2027-01-01",
]
# 日内の代表時刻（深夜・早朝・正午・直前 1 秒・端数）。
_TIMES = ["00:00:00", "01:00:00", "12:00:00", "23:59:59", "06:37:13"]


@pytest.mark.parametrize("tf", ["1W", "1M"])
@pytest.mark.parametrize("day", _DAYS)
def test_label_does_not_depend_on_time_of_day(tf: str, day: str) -> None:
    """暦日が同じなら、日内時刻が違ってもラベル日は同じ（暦日キーの根拠）。"""
    base = period_label_naive(tf, pd.Timestamp(f"{day} 00:00:00")).strftime("%Y-%m-%d")

    got = {period_label_naive(tf, pd.Timestamp(f"{day} {t}")).strftime("%Y-%m-%d")
           for t in _TIMES}

    assert got == {base}, "日内時刻でラベル日が動いてはならない（動くなら暦日キーは不正）"


@pytest.mark.parametrize("tf", ["1W", "1M"])
@pytest.mark.parametrize("day", _DAYS)
def test_memoised_label_matches_the_rule_source(tf: str, day: str) -> None:
    """記憶を通した答えが、規則源 ``period_label_naive`` の答えと一致する。"""
    ts = pd.Timestamp(f"{day} 13:45:00", tz="UTC")
    unix = int(ts.timestamp())
    b = _broker_date(unix)
    expected = period_label_naive(
        tf, pd.Timestamp(b).tz_localize(None).normalize()).strftime("%Y-%m-%d")

    assert session_period_label(tf, unix) == expected
    assert _period_label_of_broker_day(tf, b.year, b.month, b.day) == expected


@pytest.mark.parametrize("tf", ["1W", "1M"])
def test_repeated_calls_within_a_broker_day_are_stable(tf: str) -> None:
    """同一ブローカー暦日の多数の時刻で、答えが 1 つに定まる（記憶が汚れない）。"""
    start = int(pd.Timestamp("2026-08-27 00:00:00", tz="UTC").timestamp())

    labels = {session_period_label(tf, start + 60 * i) for i in range(0, 1440)}

    days = {(_broker_date(start + 60 * i).year,
             _broker_date(start + 60 * i).month,
             _broker_date(start + 60 * i).day) for i in range(0, 1440)}
    assert len(labels) == len({_period_label_of_broker_day(tf, *d) for d in days}), (
        "ブローカー暦日ごとに 1 つのラベルへ定まる")
