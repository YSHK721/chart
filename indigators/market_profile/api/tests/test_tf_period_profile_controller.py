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
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)  # ディスク無効（テスト隔離）。
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    assert st == 200 and body["ok"] is True
    assert body["tf"] == "1m" and body["from"] == 0 and body["to"] == 120
    # ISSUE-073: 1m は最小価格刻み 0.0255 でビニング（時間足別解像度・依頼者承認 2026-07-13）。
    assert body["unit"] == 0.0255
    times = [c["time"] for c in body["columns"]]
    assert times == [0, 60]
    # 0.0255 格子への量子化: mid 10→round(392.16)=392→9.996 / 11→431→10.9905 /
    # 20→784→19.992 / 21→824→21.012（price は 4 桁丸め）。
    assert body["columns"][0]["levels"] == [[9.996, 2], [10.9905, 1]]
    assert body["columns"][1]["levels"] == [[19.992, 1], [21.012, 1]]


def test_non_1m_tf_keeps_grid_w_unit(monkeypatch):
    """ISSUE-073: 1m 以外（例 15m）は ISSUE-068 の GRID_W(=10pt) を維持する。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st, body = ctl.handle_tf_period_profile("jp225_tick", "15m", 0, 900, now=1e12)
    assert st == 200 and body["ok"] is True
    assert body["unit"] == 10.0
    # 全 mids 10,10,11,20,21 が単一 15m 周期に量子化される（round(/10): 1,1,1,2,2）。
    assert body["columns"][0]["levels"] == [[10.0, 3], [20.0, 2]]


def test_completed_day_is_cached(monkeypatch):
    """完了日（day_start+86400<=now）は 2 回目以降 per-day キャッシュヒット＝tick 読込しない（ISSUE-055 B）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    calls = {"n": 0}

    def counting_ticks(symbol, start, end):
        calls["n"] += 1
        return _fake_ticks(symbol, start, end)

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", counting_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    st1, b1 = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    st2, b2 = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    assert st1 == 200 and st2 == 200
    assert calls["n"] == 1  # 2 回目はメモリキャッシュヒットで tick 読込を呼ばない（同一日）。
    assert b2["columns"] == b1["columns"]


def test_incomplete_day_not_cached(monkeypatch):
    """当日（day_start+86400>now）は成長しうるためキャッシュせず毎回再計算する（ISSUE-055 B）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", False)
    calls = {"n": 0}

    def counting_ticks(symbol, start, end):
        calls["n"] += 1
        return _fake_ticks(symbol, start, end)

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", counting_ticks)
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    # now=100 → 日 [0,86400) は未完了（86400>100）。
    ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=100)
    ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=100)
    assert calls["n"] == 2  # 当日はキャッシュされず 2 回とも再計算。


def test_completed_day_persists_to_disk(monkeypatch, tmp_path):
    """完了日はディスク JSON へ永続し、メモリ消去後（＝再起動相当）でも tick 再読込なしで復元する（ISSUE-055 B per-day）。"""
    ctl._reset_tf_period_cache()
    monkeypatch.setattr(ctl, "_TFP_CACHE_ROOT", str(tmp_path))  # ディスクを tmp に隔離。
    monkeypatch.setattr(ctl._mpd, "resolve_symbol", lambda ref: "JP225")
    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", _fake_ticks)
    st1, b1 = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)  # 計算＋ディスク保存。
    assert st1 == 200
    # 「再起動相当」: メモリを消し、tick 読込を呼べば失敗するようにする（呼ばれない＝ディスク復元の証明）。
    ctl._reset_tf_period_cache()

    def boom(*a, **k):
        raise AssertionError("tick 再読込が呼ばれた（ディスク復元されていない）")

    monkeypatch.setattr(ctl._mpd, "_load_window_ticks", boom)
    st2, b2 = ctl.handle_tf_period_profile("jp225_tick", "1m", 0, 120, now=1e12)
    assert st2 == 200
    assert b2["columns"] == b1["columns"]  # ディスクから同一結果を復元。
