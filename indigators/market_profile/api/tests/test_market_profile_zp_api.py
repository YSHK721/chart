"""GET /market_profile の src=zp 分岐（controller 純ロジック）のテスト。"""

from __future__ import annotations

import numpy as np
import pytest

from market_profile_api.compute import market_profile_zp as zp
from market_profile_api.controller.market_profile_controller import (
    _ALLOWED_SRC,
    handle_market_profile,
)

from test_market_profile_zp_store import _synth_ticks_for_day, _DAY0  # 合成ティック再利用


def test_allowed_src_contains_zp():
    assert "zp" in _ALLOWED_SRC


def test_zp_rejects_non_tick_ref():
    status, body = handle_market_profile("jp225", src="zp")
    assert status == 400
    assert body["error"]["type"] == "validation"
    assert "zp" in body["error"]["message"]


def test_unknown_src_message_lists_zp():
    status, body = handle_market_profile("jp225_tick", src="nope")
    assert status == 400
    assert "zp" in body["error"]["message"]


def test_zp_response_schema(monkeypatch, tmp_path):
    """monkeypatch 合成ティック＋合成 candles で 200 応答スキーマを固定する。"""
    def fake_load(symbol, start, end):
        s, e = int(start), int(end)
        all_s, all_m = [], []
        day = (s // 86400) * 86400
        while day < e:
            secs, mids = _synth_ticks_for_day(day)
            keep = (secs >= s) & (secs < e)
            all_s.append(secs[keep])
            all_m.append(mids[keep])
            day += 86400
        return (
            np.concatenate(all_s) if all_s else np.array([], dtype=np.int64),
            np.concatenate(all_m) if all_m else np.array([]),
        )

    monkeypatch.setattr(zp._mpd, "_load_window_ticks", fake_load)
    monkeypatch.setattr(zp, "day_parquet_files", lambda *a, **k: [])
    monkeypatch.setattr(zp, "_ZP_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(zp, "NULL_HIST_DAYS", 15)
    monkeypatch.setattr(zp, "NULL_MIN_DAYS", 8)
    monkeypatch.setattr(zp, "M_REPS_DAY", 200)
    monkeypatch.setattr(zp, "M_REPS_LIVE", 100)
    zp._reset_caches()

    # candles は dataset.load_candles を monkeypatch（controller 経由の窓確定を通す）
    import market_profile_api.controller.market_profile_controller as mpc

    t29, t30 = _DAY0 + 29 * 86400, _DAY0 + 30 * 86400
    fake_candles = [
        {"time": t29 + 3600, "open": 20000, "high": 20120, "low": 19880, "close": 20050},
        {"time": t30 + 3600, "open": 20050, "high": 20150, "low": 19900, "close": 20100},
    ]
    monkeypatch.setattr(mpc.dataset, "load_candles", lambda *a, **k: fake_candles)
    monkeypatch.setattr(mpc.dataset, "is_known", lambda ref: ref == "jp225_tick")

    status, body = handle_market_profile("jp225_tick", src="zp", sessions="1", today="1")
    zp._reset_caches()
    assert status == 200
    assert body["src"] == "zp"
    assert "超過占有" in body["atom"]
    prof = body["profile"]
    for key in ("bins", "poc", "va_low", "va_high", "price_min", "price_max",
                "tpo_units", "n_bins", "z_max", "poc_star", "today", "today_max"):
        assert key in prof, key
    assert prof["poc"] == prof["poc_star"]
    assert all(0.0 <= b["norm"] <= 1.0 for b in prof["bins"])
    assert "sessions" in body and "sessions_total" in body
    assert "sessions" not in prof  # sessions は top-level へ pop 済み
