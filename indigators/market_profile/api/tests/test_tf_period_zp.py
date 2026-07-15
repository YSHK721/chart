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
    # ISSUE-079: log 格子。unit はレンジ中央での 1 セル価格幅（≈ 価格×(e^W_LOG−1)）＝正の実数。
    assert body["unit"] > 0
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
    assert b0["unit"] > 0  # ISSUE-079: log 格子の代表価格幅。
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
    disk = tmp_path / "tfp" / "JP225" / "1h" / "s3" / "zp" / f"{_day(29)}.json"  # ISSUE-085: VA 修正 s3 世代。
    assert disk.is_file()
    data = json.loads(disk.read_text())
    assert data["unit"] > 0  # ISSUE-079: log 格子の代表価格幅。


def test_zp_live_ticks_fresh_tail(tfp_env, monkeypatch):
    """ISSUE-083 追補: 当日 zp 列は live_ticks で分足格子の末尾が最新化される（frontier 遅延の解消）。

    parquet 末尾を 5 分遅延させ（frontier）、その先のティックを live_ticks で供給すると、
    当日最終列の価格レンジ（price_max）が live 末尾の水準を反映する（ffill 停滞の解消）。
    """
    from marketdata.session_day import next_session_day_start, session_day_start

    day = session_day_start(_day(29) + 3600)
    now = day + 6 * 3600  # セッション 6 時間経過（未完了日）。
    assert next_session_day_start(day) > now
    frontier = now - 300  # parquet 末尾が 5 分遅延している想定。
    full_load = tfp._mpd._load_window_ticks  # fixture の合成ローダ。
    monkeypatch.setattr(
        tfp._mpd, "_load_window_ticks",
        lambda symbol, start, end: full_load(symbol, start, min(int(end), frontier)),
    )
    # frontier 以降のティックを buffer 末尾として供給（+0.5% の新水準＝±30% 内）。
    tail_secs, tail_mids = full_load("JP225", frontier, now)
    assert len(tail_secs) > 0, "合成データに frontier 以降のティックがあること"
    live = [(int(s) * 1000, float(m) * 1.005) for s, m in zip(tail_secs, tail_mids)]

    # 窓は live 末尾（[now-300, now)）が属する周期まで含める（now=day+6h・1h 周期）。
    st0, without = tfp.handle_tf_period_profile(
        "jp225_tick", "1h", day + 3600, now, now=now, src="zp")
    st1, withlv = tfp.handle_tf_period_profile(
        "jp225_tick", "1h", day + 3600, now, now=now, src="zp", live_ticks=live)
    assert st0 == 200 and st1 == 200
    last0 = without["columns"][-1]
    last1 = withlv["columns"][-1]
    assert last1["price_max"] > last0["price_max"], "live 末尾の新水準が当日列へ反映される"


def test_zp_week_bucket_columns(tfp_env):
    """ISSUE-086: tf=1W の zp 列＝セッション日次 {obs,mean,var} の k 空間合成→z 再計算。

    合成データの週バケットで 200・スキーマ・VA/POC の整合（レンジ内・poc が levels に含まれる）を検証。
    """
    from marketdata.session_day import session_period_label

    now = _day(40)
    label = session_period_label("1W", _day(29))
    y, m, d = (int(x) for x in label.split("-"))
    import datetime as dtm

    label_mid = int(dtm.datetime(y, m, d, tzinfo=dtm.timezone.utc).timestamp())
    st, body = tfp.handle_tf_period_profile(
        "jp225_tick", "1W", label_mid - 1, label_mid + 1, now=now, src="zp")
    assert st == 200 and body["ok"] is True
    assert len(body["columns"]) == 1, "1 バケット = 1 列"
    c = body["columns"][0]
    assert c["time"] == label_mid
    assert set(c) == {"time", "levels", "poc", "va_low", "va_high",
                      "price_min", "price_max", "tpo_units"}
    assert c["tpo_units"] > 0
    assert c["price_min"] < c["poc"] < c["price_max"] or c["price_min"] <= c["poc"] <= c["price_max"]
    assert c["price_min"] <= c["va_low"] <= c["va_high"] <= c["price_max"]
