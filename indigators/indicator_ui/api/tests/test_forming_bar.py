"""forming_bar adapter の検証 — ref/tf 判定・期間始端算出・marketdata コア委譲。

marketdata.tick_m1.forming_bar_from_ticks は monkeypatch で遮断し、adapter の責務（対象ref/tf判定・
floor(now,tf) の期間始端算出・委譲引数）を純粋に固定する。実ティック parquet は読まない。
"""

from __future__ import annotations

import pandas as pd
import pytest

from adapter.compute import forming_bar as fb


def _unix(s: str) -> int:
    return int(pd.Timestamp(s).tz_localize("UTC").timestamp())


def test_floor_freq_is_derived_from_resample_rules_single_source() -> None:
    # 規則源単一化（§4）: floor freq は TIMEFRAME_RULES から導出し再エンコードしない。
    from marketdata.resample import TIMEFRAME_RULES

    for tf in ("5m", "15m", "30m", "1h", "4h", "1D"):
        assert fb._floor_freq(tf) == TIMEFRAME_RULES[tf]  # rule 文字列がそのまま floor freq。
    assert fb._floor_freq("1m") == "min"  # rule=None（原子）は分床。
    assert fb._floor_freq("1W") is None and fb._floor_freq("1M") is None  # カレンダー周期は非対応。
    assert fb._floor_freq("9z") is None  # 未知 tf。


def test_is_tick_ref_and_supported_timeframe() -> None:
    assert fb.is_tick_ref("jp225_tick")
    assert not fb.is_tick_ref("jp225_m1")  # ローソク由来は対象外。
    assert fb.is_supported_timeframe("5m")
    assert fb.is_supported_timeframe("1D")
    assert not fb.is_supported_timeframe("1W")  # 週/月は固定floor不可で非対応。
    assert not fb.is_supported_timeframe("1M")


@pytest.mark.parametrize("tf,now,expected_start", [
    ("5m", "2025-01-02 09:07:30", "2025-01-02 09:05:00"),
    ("1h", "2025-01-02 09:40:00", "2025-01-02 09:00:00"),
    ("1D", "2025-01-02 09:40:00", "2025-01-02 00:00:00"),
    ("1m", "2025-01-02 09:07:30", "2025-01-02 09:07:00"),
])
def test_period_start_unix_floors_now_to_tf(tf, now, expected_start) -> None:
    assert fb.period_start_unix(_unix(now), tf) == _unix(expected_start)


def test_forming_bar_delegates_with_period_window(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(
        fb, "forming_bar_from_ticks",
        lambda s, e, **k: (seen.update(start=s, end=e), {"time": s, "open": 1.0,
                            "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})[1],
    )
    now = _unix("2025-01-02 09:07:30")
    bar = fb.forming_bar("jp225_tick", "5m", now)
    assert seen["start"] == _unix("2025-01-02 09:05:00")  # floor(now,5m)
    assert seen["end"] == now
    assert bar["time"] == seen["start"]


def test_forming_bar_none_for_non_tick_ref_or_unsupported_tf(monkeypatch) -> None:
    # 委譲先が呼ばれないこと（早期 None）。
    monkeypatch.setattr(fb, "forming_bar_from_ticks", lambda *a, **k: pytest.fail("呼ばれてはいけない"))
    assert fb.forming_bar("jp225_m1", "5m", _unix("2025-01-02 09:00:00")) is None  # 非tick ref
    assert fb.forming_bar("jp225_tick", "1W", _unix("2025-01-02 09:00:00")) is None  # 非対応tf
