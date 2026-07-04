"""market_profile_forming_controller と GET /market_profile_forming ルートの検証。

対象（Phase2 設計 mp_ticklive_design.md「新規 backend controller」「変更 framework」）:
  handle_market_profile_forming(ref, timeframe, since, base, now, bins, va, barw) -> (status, body)
    - base=1: forming_ticks + dwell base(to=formingStart-1) + get_active_table を束ねる。base は忠実
        binning（Task A 是正）のため GRID_W 固定グリッド（baseFine/baseKmin）で返す。
        body: {ok, formingStart, ticks, baseFine, baseKmin, activeTable, priceMin, priceMax, nBins, gridW, now}
    - base=0: forming ticks 尾部 + formingStart のみ（軽量）。
    - 非 tick ref / 非対応 tf(1W/1M) → 400 nested error（_error_body 再利用）。
    - base=1 は to=formingStart-1 で forming 期間を base から排除する（二重計上なし）。

設計方針（AAA・test_market_profile_dwell.py の流儀）:
  合成: dataset.load_candles（base 経路）と _load_window_ticks（tick 経路）を monkeypatch。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pytest

from adapter.compute import market_profile_dwell as mpd
from adapter.controller import market_profile_controller as mpc
from adapter.controller.market_profile_forming_controller import (
    handle_market_profile_forming,
)

_DAY0 = 1704067200      # 2024-01-01 00:00 UTC（月曜）。
_H2 = _DAY0 + 7200      # hr2:00（floor(1h) 境界＝formingStart）。
_HOT0 = 1005.0
_HOT1 = 1015.0
_FORM = 1025.0          # forming 期間にのみ現れる価格（base 排除の証拠）。


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(mpd, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(mpd, "_day_source_signature", lambda symbol, day_start: "")
    mpd._reset_caches()
    yield
    mpd._reset_caches()


def _master():
    """hr0 に _HOT0、hr1 に _HOT1（確定足）、forming(hr2) に _FORM を密集配置した合成 tick。"""
    rows = []
    for i in range(30):
        rows.append((_DAY0 + 0 + 10 * i, _HOT0))       # hr0
        rows.append((_DAY0 + 3600 + 10 * i, _HOT1))     # hr1
        rows.append((_H2 + 10 * i, _FORM))              # hr2（forming）
    return rows


def _inject(monkeypatch):
    master = _master()
    s = np.array([t for t, _ in master], dtype=np.int64)
    m = np.array([p for _, p in master], dtype=np.float64)

    def _loader(symbol, start, end):
        win = (s >= int(start)) & (s < int(end))
        s2, m2 = s[win], m[win]
        order = np.argsort(s2, kind="stable")
        return s2[order], m2[order]

    monkeypatch.setattr(mpd, "_load_window_ticks", _loader)
    # base 経路（handle_market_profile src=dwell）が使う候補足。hr0/hr1（確定）＋ forming(hr2)。
    candles = [
        {"time": _DAY0, "open": _HOT0, "high": _HOT0, "low": _HOT0, "close": _HOT0},
        {"time": _DAY0 + 3600, "open": _HOT1, "high": _HOT1, "low": _HOT1, "close": _HOT1},
        {"time": _H2, "open": _FORM, "high": _FORM, "low": _FORM, "close": _FORM},
    ]
    monkeypatch.setattr(mpc.dataset, "load_candles", lambda ref, tf, limit: candles)
    return candles


_NOW = _H2 + 200  # hr2 の途中（formingStart = floor = _H2）。


class TestValidation:
    def test_non_tick_ref_returns_400(self, monkeypatch):
        # Arrange / Act
        status, body = handle_market_profile_forming("sample", "1h", now=_NOW)
        # Assert
        assert status == 400
        assert body["error"]["type"] == "validation"

    def test_unsupported_tf_1w_returns_400(self, monkeypatch):
        status, body = handle_market_profile_forming("jp225_tick", "1W", now=_NOW)
        assert status == 400
        assert body["error"]["type"] == "validation"

    def test_unsupported_tf_1m_returns_400(self, monkeypatch):
        status, body = handle_market_profile_forming("jp225_tick", "1M", now=_NOW)
        assert status == 400
        assert body["error"]["type"] == "validation"


class TestBaseFull:
    def test_base1_bundles_forming_ticks_base_and_active_table(self, monkeypatch):
        # Arrange
        _inject(monkeypatch)
        # Act
        status, body = handle_market_profile_forming("jp225_tick", "1h", base=1, now=_NOW)
        # Assert: 応答 shape（base=1 は base/activeTable を含む）。
        assert status == 200 and body["ok"] is True
        assert body["formingStart"] == _H2
        assert body["now"] == _NOW
        # base は GRID_W 固定グリッド（fine grid）。長さ = floor(priceMax/gridW)-floor(priceMin/gridW)+1。
        assert isinstance(body["baseFine"], list)
        import math
        expected_size = (math.floor(body["priceMax"] / body["gridW"])
                         - math.floor(body["priceMin"] / body["gridW"]) + 1)
        assert len(body["baseFine"]) == expected_size
        assert body["baseKmin"] == math.floor(body["priceMin"] / body["gridW"])
        assert len(body["activeTable"]) == 7 and all(len(r) == 24 for r in body["activeTable"])
        assert body["gridW"] == mpd.GRID_W
        assert body["priceMin"] is not None and body["priceMax"] is not None
        # forming 期間の tick（_FORM）が [formingStart, now) に含まれる。
        assert body["ticks"] and all(t[1] == _FORM for t in body["ticks"])

    def test_base1_excludes_forming_period_no_double_count(self, monkeypatch):
        # Arrange
        _inject(monkeypatch)
        # Act: base（GRID_W 固定グリッド）は to=formingStart-1 の dwell fine grid（＝forming 期間を除外）と
        #   厳密一致するはず。参照も want_fine=True で fine grid を取り比較する（二重計上なしの実証）。
        status, body = handle_market_profile_forming("jp225_tick", "1h", base=1, now=_NOW)
        _, ref_body = mpc.handle_market_profile(
            "jp225_tick", timeframe="1h", src="dwell", to=_H2 - 1, want_fine=True,
        )
        ref_fine = ref_body["profile"]["fine"]
        ref_kmin = ref_body["profile"]["fine_kmin"]
        # Assert: baseFine/baseKmin が to=formingStart-1 の dwell fine grid と厳密一致（二重計上なし）。
        assert body["baseFine"] == ref_fine
        assert body["baseKmin"] == ref_kmin
        # forming 期間の価格 _FORM は base（確定足まで）の fine grid 範囲外＝base に混入しない（forming 排除の実証）。
        k_form = int(_FORM // mpd.GRID_W)
        assert not (ref_kmin <= k_form < ref_kmin + len(ref_fine))


class TestBaseLight:
    def test_base0_returns_ticks_and_forming_start_only(self, monkeypatch):
        # Arrange
        _inject(monkeypatch)
        # Act
        status, body = handle_market_profile_forming("jp225_tick", "1h", base=0, now=_NOW)
        # Assert: 軽量応答（base/activeTable を含まない）。
        assert status == 200 and body["ok"] is True
        assert body["formingStart"] == _H2
        assert body["ticks"] and all(t[1] == _FORM for t in body["ticks"])
        assert "baseFine" not in body
        assert "baseKmin" not in body
        assert "activeTable" not in body

    def test_since_filters_forming_ticks_tail(self, monkeypatch):
        # Arrange
        _inject(monkeypatch)
        # Act: since=最初の forming tick 秒 → 尾部のみ返る（差分取得）。
        first_sec = _H2  # 最初の forming tick。
        status, body = handle_market_profile_forming(
            "jp225_tick", "1h", since=first_sec, base=0, now=_NOW,
        )
        # Assert: sec>since のみ（先頭を除外）。
        assert all(t[0] > first_sec for t in body["ticks"])


# --------------------------------------------------------------------------- #
# 配線スモーク: GET /market_profile_forming（薄殻の 1 本分岐が handler へ届くか）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def server():
    from framework.server import IndicatorUIRequestHandler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), IndicatorUIRequestHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(base: str, path: str):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_route_unknown_ref_returns_400(server):
    # 非 tick ref はルート経由でも 400 validation。
    status, payload = _get(server, "/market_profile_forming?datasetRef=sample&timeframe=1h")
    assert status == 400
    assert payload["error"]["type"] == "validation"


def test_route_returns_forming_payload(server):
    # 実データ jp225_tick・1h・now 明示。base=0 で軽量に formingStart/ticks/now を返す。
    status, payload = _get(
        server, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h&base=0&now=%d" % _NOW,
    )
    assert status == 200
    assert payload["ok"] is True
    assert payload["formingStart"] == _H2
    assert "ticks" in payload and "now" in payload
