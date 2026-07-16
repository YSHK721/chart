"""session_period_label の resample 委譲の byte 不変検証（ISSUE-094 🟡-10a）。

週/月ラベルの手書き暦算術を resample.period_label_naive への単方向委譲へ構造変更した後、
出力が旧手書き実装と byte 不変であることを、DST 境界・週/月端を含む広範囲で固定する。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from marketdata.session_day import _BROKER_SHIFT, session_period_label

_NY = ZoneInfo("America/New_York")


def _broker_date(t):
    return datetime.fromtimestamp(float(t), tz=_NY) + _BROKER_SHIFT


def _old_hand_label(tf: str, t) -> str:
    """委譲前の手書き暦算術（回帰の基準・byte 比較用に温存複製）。"""
    b = _broker_date(t)
    if tf == "1W":
        lab = b + timedelta(days=(4 - b.weekday()) % 7)
    elif tf == "1M":
        nxt = (b.replace(day=1) + timedelta(days=32)).replace(day=1)
        lab = nxt - timedelta(days=1)
    else:
        raise ValueError(tf)
    return lab.strftime("%Y-%m-%d")


def test_delegation_is_byte_identical_across_years():
    # 2012..2026 を 6 時間刻みで走査（DST 切替・週/月端・大晦日/元日を含む）。
    start = int(datetime(2012, 1, 1, tzinfo=_NY).timestamp())
    end = int(datetime(2026, 12, 31, tzinfo=_NY).timestamp())
    step = 6 * 3600
    t = start
    while t <= end:
        for tf in ("1W", "1M"):
            assert session_period_label(tf, t) == _old_hand_label(tf, t), (tf, t)
        t += step


def test_rejects_unknown_tf():
    with pytest.raises(ValueError):
        session_period_label("1D", 1_700_000_000)


def test_no_import_cycle_session_day_resample():
    # session_day → resample は非循環（resample は pandas のみに依存する葉）。
    import ast
    import inspect

    from marketdata import resample

    tree = ast.parse(inspect.getsource(resample))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "session_day" not in node.module
        if isinstance(node, ast.Import):
            assert all("session_day" not in a.name for a in node.names)
