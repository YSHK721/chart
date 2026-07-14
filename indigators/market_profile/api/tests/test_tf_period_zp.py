"""tf_period_profile の src=zp 拡張テスト（合成ティック・キー分離・byte 不変）。"""

from __future__ import annotations

import json

import numpy as np
import pytest

import market_profile_api.controller.tf_period_profile_controller as tfp
from market_profile_api.compute import market_profile_zp as zp

from test_market_profile_zp_store import _synth_ticks_for_day, _DAY0


@pytest.fixture()
def tfp_env(monkeypatch, tmp_path):
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

    monkeypatch.setattr(tfp._mpd, "_load_window_ticks", fake_load)
    monkeypatch.setattr(zp, "day_parquet_files", lambda *a, **k: [])
    monkeypatch.setattr(zp, "_ZP_CACHE_ROOT", tmp_path / "zpcache")
    monkeypatch.setattr(tfp, "_TFP_CACHE_ROOT", tmp_path / "tfp")
    monkeypatch.setattr(zp, "NULL_HIST_DAYS", 15)
    monkeypatch.setattr(zp, "NULL_MIN_DAYS", 8)
    monkeypatch.setattr(zp, "M_REPS_DAY", 200)
    monkeypatch.setattr(zp, "M_REPS_LIVE", 100)
    zp._reset_caches()
    tfp._reset_tf_period_cache()
    yield
    zp._reset_caches()
    tfp._reset_tf_period_cache()


def _day(n: int) -> int:
    return _DAY0 + n * 86400


def test_zp_rejects_small_tf(tfp_env):
    for tf in ("1m", "5m"):
        status, body = tfp.handle_tf_period_profile(
            "jp225_tick", tf, _day(29), _day(30), now=_day(40), src="zp")
        assert status == 400
        assert "zp" in body["error"]["message"]


def test_unknown_src_rejected(tfp_env):
    status, body = tfp.handle_tf_period_profile(
        "jp225_tick", "1h", _day(29), _day(30), now=_day(40), src="nope")
    assert status == 400


def test_zp_columns_schema_and_unit(tfp_env):
    status, body = tfp.handle_tf_period_profile(
        "jp225_tick", "1h", _day(29), _day(30), now=_day(40), src="zp")
    assert status == 200
    assert body["unit"] == float(zp.GRID_W)
    assert body["columns"], "1h 周期列が生成されること"
    for c in body["columns"]:
        assert set(c) == {"time", "levels", "poc", "va_low", "va_high",
                          "price_min", "price_max", "tpo_units"}
        assert c["tpo_units"] > 0
        assert all(len(lv) == 2 for lv in c["levels"])
    # 周期数: セッション窓に重なる 1h 周期（01:00〜24:00 → 23 本）
    assert len(body["columns"]) == 23


def test_src_none_response_unchanged_and_keys_separated(tfp_env):
    """src=None は従来経路（count 列）。zp とはメモリキー・ディスク subdir で分離される。"""
    now = _day(40)
    s0, b0 = tfp.handle_tf_period_profile("jp225_tick", "1h", _day(29), _day(30), now=now)
    assert s0 == 200
    # ISSUE-068: count 列も GRID_W(=10pt) グリッド。値は整数カウント（zp の z 値と別物）。
    assert b0["unit"] == float(zp.GRID_W)
    assert b0["columns"], "count 列が生成される"
    assert all(isinstance(lv[1], (int,)) or float(lv[1]).is_integer()
               for c in b0["columns"] for lv in c["levels"]), "count は整数"
    s1, _ = tfp.handle_tf_period_profile(
        "jp225_tick", "1h", _day(29), _day(30), now=now, src="zp")
    assert s1 == 200
    # メモリキーが (sym, tf, day, "zp") で分離され、素の再取得が zp に汚染されない
    s2, b2 = tfp.handle_tf_period_profile("jp225_tick", "1h", _day(29), _day(30), now=now)
    assert b2 == b0


def test_zp_disk_cache_subdir(tfp_env, tmp_path):
    now = _day(40)
    tfp.handle_tf_period_profile("jp225_tick", "1h", _day(29), _day(30), now=now, src="zp")
    disk = tmp_path / "tfp" / "JP225" / "1h" / "s1" / "zp" / f"{_day(29)}.json"  # ISSUE-078: s1 世代。
    assert disk.is_file()
    data = json.loads(disk.read_text())
    assert data["unit"] == float(zp.GRID_W)
