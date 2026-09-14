"""marketdata.tf_meta（ISSUE-087 🔴-1/🔴-2: tf メタ・tick ref・期間始端の単一情報源）の検証。

移設元 indicator_ui/api/adapter/compute/forming_bar.py の純関数群と同一挙動であること
（規則源 TIMEFRAME_RULES / session_day への委譲）を固定する。
"""
from __future__ import annotations

from datetime import datetime, timezone

from marketdata import tf_meta


def utc(y, m, d, hh=0, mm=0, ss=0):
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp())


def test_tick_ref_whitelist():
    assert tf_meta.is_tick_ref("jp225_tick") is True
    assert tf_meta.is_tick_ref("jp225_m1") is False
    assert tf_meta.is_tick_ref(None) is False


def test_supported_timeframes_floorable_only():
    for tf in ("1m", "5m", "15m", "30m", "1h", "4h", "1D"):
        assert tf_meta.is_supported_timeframe(tf) is True, tf
    for tf in ("1W", "1M", "3h", None):
        assert tf_meta.is_supported_timeframe(tf) is False, tf


def test_period_start_unix_utc_floor_and_session_day():
    now = utc(2026, 7, 15, 12, 34, 56)
    assert tf_meta.period_start_unix(now, "1m") == utc(2026, 7, 15, 12, 34)
    assert tf_meta.period_start_unix(now, "1h") == utc(2026, 7, 15, 12)
    # 1D はセッション日始端（夏 EDT＝前日 21:00 UTC・ISSUE-078）。
    assert tf_meta.period_start_unix(now, "1D") == utc(2026, 7, 14, 21)


def test_tf_bar_sec_covers_all_timeframes():
    # 単一情報源: TIMEFRAME_RULES の全 tf を被覆し、既知の秒長と一致する。
    from marketdata.resample import TIMEFRAME_RULES

    assert set(tf_meta.TF_BAR_SEC) == set(TIMEFRAME_RULES)
    assert tf_meta.TF_BAR_SEC["1m"] == 60
    assert tf_meta.TF_BAR_SEC["30m"] == 1800
    assert tf_meta.TF_BAR_SEC["1D"] == 86400
    assert tf_meta.TF_BAR_SEC["1W"] == 604800


def test_resolve_now_unix_override_precedence():
    assert tf_meta.resolve_now_unix(1234567890) == 1234567890
    assert isinstance(tf_meta.resolve_now_unix(None), int)


def test_error_status_contract():
    from api_shared.http_contract import ERROR_STATUS

    assert ERROR_STATUS["validation"] == 400
    assert ERROR_STATUS["empty_series"] == 422
    assert ERROR_STATUS["internal"] == 500
