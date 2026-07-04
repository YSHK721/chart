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


class TestSessionWindowFrom:
    """base の ``frm``（当日始まり=floor(now,86400)）指定で base 累積が当日窓へ収束することを固定する。

    仕様（セッション窓 MP）: base の累積下限 ``from = 当日始まり``。combined = [当日始まり, now) ＝古典的
    Market Profile（1 日の TPO 形成）。価格域が当日帯へ集中する。``frm=None``（present-mode・省略）は
    従来どおり全期間 base＝不変（後方互換）。
    """

    def _inject2days(self, monkeypatch):
        """day A(~1005-1010 帯) と day B(~2005-2025 帯) の 2 日分を合成注入する（価格帯を明確に分離）。"""
        dayb = _DAY0 + 86400
        rows = []
        for i in range(30):
            rows.append((_DAY0 + 10 * i, 1005.0))           # dayA hr0
            rows.append((_DAY0 + 3600 + 10 * i, 1010.0))    # dayA hr1
            rows.append((dayb + 10 * i, 2005.0))            # dayB hr0
            rows.append((dayb + 3600 + 10 * i, 2010.0))     # dayB hr1
            rows.append((dayb + 7200 + 10 * i, 2025.0))     # dayB hr2（forming）
        s = np.array([t for t, _ in rows], dtype=np.int64)
        m = np.array([p for _, p in rows], dtype=np.float64)

        def _loader(symbol, start, end):
            win = (s >= int(start)) & (s < int(end))
            s2, m2 = s[win], m[win]
            order = np.argsort(s2, kind="stable")
            return s2[order], m2[order]

        monkeypatch.setattr(mpd, "_load_window_ticks", _loader)
        candles = [
            {"time": _DAY0, "open": 1005.0, "high": 1005.0, "low": 1005.0, "close": 1005.0},
            {"time": _DAY0 + 3600, "open": 1010.0, "high": 1010.0, "low": 1010.0, "close": 1010.0},
            {"time": dayb, "open": 2005.0, "high": 2005.0, "low": 2005.0, "close": 2005.0},
            {"time": dayb + 3600, "open": 2010.0, "high": 2010.0, "low": 2010.0, "close": 2010.0},
            {"time": dayb + 7200, "open": 2025.0, "high": 2025.0, "low": 2025.0, "close": 2025.0},
        ]
        monkeypatch.setattr(mpc.dataset, "load_candles", lambda ref, tf, limit: candles)
        return dayb

    def test_frm_session_window_breakout_union_range_no_clip(self, monkeypatch):
        # Arrange: now を day B の hr2 途中に置く（formingStart = day B hr2）。forming(2025) は base 当日
        #   既経過レンジ(2005-2010)の外へ抜けるブレイクアウト。修正後は base 非空レンジ ∪ forming tick
        #   実測 min/max の和集合レンジで clip されず forming tick が fine grid 内に配置される（残存リスク#2 解消）。
        dayb = self._inject2days(monkeypatch)
        now = dayb + 7200 + 200
        frm = (now // 86400) * 86400  # == 当日始まり(day B) = floor(now,86400)。
        # Act
        status, body = handle_market_profile_forming(
            "jp225_tick", "1h", base=1, now=now, frm=frm,
        )
        # Assert: base は当日(day B)の経過足のみ＝下限は当日帯(2005)へ収束（day A 1005-1010 を除外）。
        #   上限は forming ブレイクアウト(2025)まで拡張（和集合レンジ・clip なし）。
        assert status == 200 and body["ok"] is True
        assert body["priceMin"] == 2005.0            # 当日帯下限（day A 除外は維持）。
        assert body["priceMax"] == 2025.0            # forming ブレイクアウトを含む（和集合上限）。
        import math
        gw = body["gridW"]
        assert body["baseKmin"] == math.floor(2005.0 / gw)  # 200（当日帯下限＝floor(priceMin/gridW)）。
        # baseFine は [floor(priceMin/gw), floor(priceMax/gw)] を覆う（forming k を含むよう右へ zero-pad 拡張）。
        assert len(body["baseFine"]) == (
            math.floor(2025.0 / gw) - math.floor(2005.0 / gw) + 1
        )
        # forming tick(2025) の fine bin k が baseFine グリッド [kmin, kmin+len) 内＝clip されない（実証）。
        k_form = math.floor(2025.0 / gw)
        assert body["baseKmin"] <= k_form < body["baseKmin"] + len(body["baseFine"])
        # base 当日既経過ぶん(2005/2010)は baseFine に温存（左側 non-zero）＝二重計上なし・base 温存の両立。
        assert body["baseFine"][0] > 0 or body["baseFine"][1] > 0
        # forming tick は now 由来で不変（day B hr2 の 2025・from の影響を受けない）。
        assert body["ticks"] and all(t[1] == 2025.0 for t in body["ticks"])

    def test_frm_none_is_full_period_backward_compat(self, monkeypatch):
        # Arrange: 同一データ・同一 now。frm 省略（present-mode 相当）。
        dayb = self._inject2days(monkeypatch)
        now = dayb + 7200 + 200
        # Act: frm を渡さない＝従来全期間 base（後方互換）。
        status, body = handle_market_profile_forming("jp225_tick", "1h", base=1, now=now)
        # Assert: day A(1005) 〜 day B(2010) を含む広い価格域＝当日収束前の現行挙動。
        assert status == 200 and body["ok"] is True
        assert body["priceMin"] == 1005.0
        assert body["priceMax"] == 2010.0
        import math
        assert body["baseKmin"] == math.floor(1005.0 / body["gridW"])  # 100（全期間帯）。

    def test_frm_none_base_fields_byte_identical_to_direct_base(self, monkeypatch):
        # Arrange: frm=None（present-mode）では base 関連フィールドが直接 base 経路
        #   （handle_market_profile src=dwell to=formingStart-1 want_fine）と byte 同一であること
        #   ＝レンジ導出ロジックが present-mode を一切汚染しないことの回帰実証。
        dayb = self._inject2days(monkeypatch)
        now = dayb + 7200 + 200
        forming_start = dayb + 7200
        # Act
        status, body = handle_market_profile_forming("jp225_tick", "1h", base=1, now=now)
        _, ref_body = mpc.handle_market_profile(
            "jp225_tick", timeframe="1h", src="dwell", to=forming_start - 1, want_fine=True,
        )
        rp = ref_body["profile"]
        # Assert: baseFine/baseKmin/priceMin/priceMax/nBins/gridW が直接 base 経路と厳密一致（不干渉）。
        assert status == 200
        assert body["baseFine"] == rp["fine"]
        assert body["baseKmin"] == rp["fine_kmin"]
        assert body["priceMin"] == rp["price_min"]
        assert body["priceMax"] == rp["price_max"]
        assert body["nBins"] == rp["n_bins"]
        assert body["gridW"] == rp["grid_w"]


class TestSessionWindow1DEmptyBase:
    """1D セッション窓の欠陥修正: base 空でも forming（当日全 tick）で当日プロファイルが育つ。

    欠陥: 1D は formingStart == frm(当日始まり) のため base 窓 [frm, formingStart) が空 → 縮退レンジ
        (priceMin=0/priceMax=1・baseKmin=0/size=1) → 当日 tick(mid≈68000)が全 clip → MP 空。
    修正: frm!=None 時は base 非空レンジ ∪ forming tick(mid) 実測 min/max の和集合からレンジ導出し、
        base 空なら forming tick レンジのみで baseFine を zero-padded（導出レンジを覆う長さの零配列）にする。
    """

    _DAY_1D = 1782950400   # floor(1782985000, 86400)（当日始まり）。
    _NOW_1D = 1782985000   # 当日 34600 秒経過（task 実測 now）。
    _MID_LO = 68280.0
    _MID_HI = 69894.0

    def _inject1d(self, monkeypatch):
        """当日 [DAY_1D, NOW_1D) に mid 68280..69894 を張る forming tick を注入する（base 窓は常に空）。"""
        n = 20
        secs = [self._DAY_1D + 100 + i * 1500 for i in range(n)]  # 当日内・now 未満。
        mids = [self._MID_LO + (self._MID_HI - self._MID_LO) * i / (n - 1) for i in range(n)]
        mids[0], mids[-1] = self._MID_LO, self._MID_HI  # 端点を厳密化。
        s = np.array(secs, dtype=np.int64)
        m = np.array(mids, dtype=np.float64)

        def _loader(symbol, start, end):
            win = (s >= int(start)) & (s < int(end))
            s2, m2 = s[win], m[win]
            order = np.argsort(s2, kind="stable")
            return s2[order], m2[order]

        monkeypatch.setattr(mpd, "_load_window_ticks", _loader)
        # 当日足のみ（1D）。base 窓 [frm=DAY_1D, formingStart-1=DAY_1D-1] は空集合＝どの足も入らない。
        candles = [
            {"time": self._DAY_1D, "open": self._MID_LO, "high": self._MID_HI,
             "low": self._MID_LO, "close": self._MID_HI},
        ]
        monkeypatch.setattr(mpc.dataset, "load_candles", lambda ref, tf, limit: candles)

    def test_1d_empty_base_range_derived_from_forming_ticks_no_clip(self, monkeypatch):
        # Arrange
        self._inject1d(monkeypatch)
        frm = (self._NOW_1D // 86400) * 86400  # == DAY_1D == formingStart(1D)。
        # Act
        status, body = handle_market_profile_forming(
            "jp225_tick", "1D", base=1, now=self._NOW_1D, frm=frm,
        )
        # Assert: base 空でも forming tick レンジで priceMin/priceMax が当日レンジへ収束。
        assert status == 200 and body["ok"] is True
        assert body["priceMin"] == self._MID_LO      # 68280（forming 実測下限）。
        assert body["priceMax"] == self._MID_HI      # 69894（forming 実測上限）。
        import math
        gw = body["gridW"]
        assert body["baseKmin"] == math.floor(self._MID_LO / gw)  # 6828。
        expected_size = math.floor(self._MID_HI / gw) - math.floor(self._MID_LO / gw) + 1
        assert len(body["baseFine"]) == expected_size            # 162。
        # base 空＝baseFine は zero-padded（全ゼロ）。
        assert all(v == 0.0 for v in body["baseFine"])
        # 全 forming tick の fine bin k が baseFine グリッド内＝addTick で clip されない（MP が育つ前提）。
        kmin = body["baseKmin"]
        for _, mid in body["ticks"]:
            k = math.floor(mid / gw)
            assert kmin <= k < kmin + len(body["baseFine"])


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
