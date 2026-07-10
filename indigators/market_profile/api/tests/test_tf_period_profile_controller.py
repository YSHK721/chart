"""tf_period_profile_controller（GET /tf_period_profile の純ロジック）の検証。

handle_tf_period_profile(ref, timeframe, frm, to) -> (status, body)
  - 非 tick ref / 非対応 tf（1W/1M/未知）→ 400。
  - 不正窓（from>=to / 欠落）→ 400。
  - 正常: 窓 tick を最小単位でビニングした tf-period 列を返す（tick 読込は monkeypatch）。
"""
from __future__ import annotations

import numpy as np

from market_profile_api.controller import tf_period_profile_controller as ctl


def _fake_ticks(_symbol, start, end):
    # 2 周期分（1m）: period0[0..59] mids 10,10,11 / period60[60..119] mids 20,21。
    secs = np.array([0, 10, 20, 60, 70])
    mids = np.array([10.0, 10.0, 11.0, 20.0, 21.0])
    m = (secs >= start) & (secs < end)
    return secs[m], mids[m]


def test_non_tick_ref_400():
    st, body = ctl.handle_tf_period_profile("jp225_m1", "1m", 0, 120)
    assert st == 400 and body["ok"] is False


def test_unsupported_tf_400():
    st, body = ctl.handle_tf_period_profile("jp225_tick", "1W", 0, 120)
    assert st == 400


def test_bad_window_400():
    st, _ = ctl.handle_tf_period_profile("jp225_tick", "1m", 120, 120)  # from>=to
    assert st == 400
    st2, _ = ctl.handle_tf_period_profile("jp225_tick", "1m", None, 120)  # 欠落
    assert st2 == 400


def test_happy_path_returns_columns(monkeypatch):
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120)
    assert st == 200 and body["ok"] is True
    assert body["tf"] == "1m" and body["from"] == 0 and body["to"] == 120
    assert body["unit"] == 1.0  # 最小 mid 増分（10,11 の差=1）。
    times = [c["time"] for c in body["columns"]]
    assert times == [0, 60]
    assert body["columns"][0]["levels"] == [[10.0, 2], [11.0, 1]]
