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


def _capture_zp_now(monkeypatch):
    """compute_zp_profile を捕捉 fake に差し替え、受領 kwargs 記録 dict を返す（ISSUE-129 検証用）。"""
    import market_profile_api.controller.market_profile_controller as mpc

    captured: dict = {}

    def fake_compute(symbol, t0, t1, price_min, price_max, n_bins, **kwargs):
        captured.update(kwargs)
        return {
            "bins": [], "poc": None, "va_low": None, "va_high": None,
            "price_min": price_min, "price_max": price_max,
            "tpo_units": 0, "n_bins": n_bins,
        }

    monkeypatch.setattr(mpc.market_profile_zp, "compute_zp_profile", fake_compute)
    monkeypatch.setattr(
        mpc.dataset, "load_candles",
        lambda *a, **k: [
            {"time": _DAY0 + 3600, "open": 20000, "high": 20100, "low": 19900, "close": 20050},
        ],
    )
    monkeypatch.setattr(mpc.dataset, "is_known", lambda ref: ref == "jp225_tick")
    return captured


def test_zp_to_is_the_clock(monkeypatch):
    """ISSUE-129（単一時計）: to 指定時、compute の「現在時刻」now が to（リプレイ現在時刻）になる。"""
    captured = _capture_zp_now(monkeypatch)
    to = _DAY0 + 30 * 86400 + 12 * 3600
    status, body = handle_market_profile("jp225_tick", src="zp", to=str(to))
    assert status == 200 and body["ok"] is True
    assert captured.get("now") == float(to)


def test_zp_without_to_keeps_wall_clock(monkeypatch):
    """ISSUE-129 後方互換: to 省略（ライブ＝全期間）は now を渡さない（実時計）。"""
    captured = _capture_zp_now(monkeypatch)
    status, body = handle_market_profile("jp225_tick", src="zp")
    assert status == 200 and body["ok"] is True
    assert "now" not in captured


def test_zp_legacy_asof_param_is_ignored(monkeypatch):
    """ISSUE-129: 旧 asof パラメータは廃止＝受信しても無視（エラーにも now にもしない）。"""
    captured = _capture_zp_now(monkeypatch)
    status, body = handle_market_profile("jp225_tick", src="zp", asof=str(_DAY0))
    assert status == 200 and body["ok"] is True
    assert "now" not in captured


def _setup_zp_synthetic(monkeypatch, tmp_path):
    """test_zp_response_schema と同一の合成ティック＋合成 candles 環境（ISSUE-127 再現用）。"""
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

    import market_profile_api.controller.market_profile_controller as mpc

    t29, t30 = _DAY0 + 29 * 86400, _DAY0 + 30 * 86400
    fake_candles = [
        {"time": t29 + 3600, "open": 20000, "high": 20120, "low": 19880, "close": 20050},
        {"time": t30 + 3600, "open": 20050, "high": 20150, "low": 19900, "close": 20100},
    ]
    monkeypatch.setattr(mpc.dataset, "load_candles", lambda *a, **k: fake_candles)
    monkeypatch.setattr(mpc.dataset, "is_known", lambda ref: ref == "jp225_tick")
    return t29, t30


def test_zp_future_session_contributes_nothing(monkeypatch, tmp_path):
    """ISSUE-128: now（as-of）より未来に始まるセッション日は rollup 寄与なし（None）。

    ガードが無いと ``max(1, elapsed)`` の下限 1 が未来セッションの最初の 1 分を観測へ混入させる
    （as-of 因果違反＝未訪問価格帯にバーが立つ）。
    """
    _setup_zp_synthetic(monkeypatch, tmp_path)
    day = zp.session_day_start(_DAY0 + 29 * 86400 + 3600)
    future_day = zp.next_session_day_start(day)
    now = float(day + 7200)  # 現セッション内＝future_day は未来
    assert zp._zp_day_rollup("jp225_tick", future_day, now) is None
    assert zp._zp_partial_rollup("jp225_tick", future_day, future_day + 10800, now) is None
    zp._reset_caches()


def test_zp_partial_not_poisoned_by_completed_window_cache(monkeypatch, tmp_path):
    """ISSUE-127/129: 完了窓（now>=hi）の要求が memo 化した全日 partial roll を、以後の
    未完了窓（now<hi）要求が同 (lo,hi) キーで受け取って全日確定形に化けない（now ゲート）。

    再現順が本体: ①to=窓完了後（全日）→ ②to=日内（部分）。修正前は②が①の
    キャッシュを返し byte 同一（tpo_units 同値）になる。
    """
    t29, t30 = _setup_zp_synthetic(monkeypatch, tmp_path)
    frm = t30  # 最終 candle（t30+3600）の属日だけを窓に＝partial rollup 経路（lo_t != day）。
    common = {"src": "zp", "from": str(frm)}
    # ① to=窓完了後（now=to>=hi）＝全日。partial roll が (lo,hi) キーで memo 化される。
    status1, body1 = handle_market_profile(
        "jp225_tick", to=str(t30 + 3 * 86400), **common
    )
    assert status1 == 200, body1
    full_units = body1["profile"]["tpo_units"]
    # ② to=セッション日内（now=to<hi＝部分）。キャッシュ毒なら full_units と同値になる。
    status2, body2 = handle_market_profile(
        "jp225_tick", to=str(t30 + 6 * 3600), **common
    )
    zp._reset_caches()
    assert status2 == 200, body2
    partial_units = body2["profile"]["tpo_units"]
    assert partial_units < full_units, (
        f"as-of 部分集計が完了窓キャッシュに毒されている: partial={partial_units} full={full_units}"
    )
