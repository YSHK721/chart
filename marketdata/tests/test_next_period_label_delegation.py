"""next_period_label の月末算術 → resample.period_label_naive 委譲の byte 不変検証（ISSUE-134）。

翌バケットラベルの手書き暦算術（1M の翌月末算術＝resample.py の pandas ME offset の二重表現）を
規則源 :func:`marketdata.resample.period_label_naive` への委譲へ構造変更した後、出力が旧手書き実装と
byte 不変であることを、月末・年跨ぎ・閏 2 月を含む全バケットラベルで固定する。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from marketdata.resample import period_label_naive
from marketdata.session_day import next_period_label


def _old_hand_next_label(tf: str, label: str) -> str:
    """委譲前の手書き暦算術（回帰の基準・byte 比較用に温存複製）。"""
    y, m, d = (int(x) for x in str(label).split("-"))
    cur = datetime(y, m, d).date()
    if tf == "1W":
        nxt = cur + timedelta(days=7)
    elif tf == "1M":
        first_next = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        nxt = (first_next + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    else:
        raise ValueError(tf)
    return nxt.strftime("%Y-%m-%d")


def _valid_bucket_labels():
    """実際の入力ドメイン（有効なバケットラベル）を規則源から列挙する。

    1W: 全金曜（W-FRI ラベル） / 1M: 全暦月末（ME ラベル）。2012..2030 を被覆。
    """
    import pandas as pd

    fridays = pd.date_range("2012-01-01", "2030-12-31", freq="W-FRI")
    month_ends = pd.date_range("2012-01-01", "2030-12-31", freq="ME")
    for ts in fridays:
        yield "1W", ts.strftime("%Y-%m-%d")
    for ts in month_ends:
        yield "1M", ts.strftime("%Y-%m-%d")


def test_delegation_is_byte_identical_over_valid_labels():
    for tf, label in _valid_bucket_labels():
        assert next_period_label(tf, label) == _old_hand_next_label(tf, label), (tf, label)


def test_boundary_month_year_leap():
    # 明示境界（既存 test_session_day と同値・回帰の可視化）。
    assert next_period_label("1W", "2026-12-25") == "2027-01-01"
    assert next_period_label("1M", "2026-12-31") == "2027-01-31"  # 年跨ぎ。
    assert next_period_label("1M", "2026-01-31") == "2026-02-28"  # 非閏 2 月。
    assert next_period_label("1M", "2028-01-31") == "2028-02-29"  # 閏 2 月。
    assert next_period_label("1M", "2026-03-31") == "2026-04-30"
    assert next_period_label("1M", "2025-11-30") == "2025-12-31"


def test_rejects_unknown_tf():
    with pytest.raises(ValueError):
        next_period_label("1D", "2026-07-17")


def test_delegates_to_period_label_naive_authority():
    """1M の翌月末は period_label_naive（ME 権威）と一致（規則の単一源）。"""
    import pandas as pd

    # 2026-01-31 の翌月末 = period_label_naive("1M", 翌月 1 日)。
    got = next_period_label("1M", "2026-01-31")
    authority = period_label_naive("1M", pd.Timestamp("2026-02-01")).strftime("%Y-%m-%d")
    assert got == authority == "2026-02-28"
