"""HTTP サーバ殻（framework/server.py）のスモーク統合テスト。

純ロジック（handle_compute / dataset.load_candles）は別テストで網羅済みのため、本テストは
「殻が正しく配線されているか」のみを socket 経由で確認する（§設計: サーバ殻はスモーク可）。

検証:
  - POST /compute（既知指標）→ 200 / ok / series。
  - POST /compute（壊れた JSON）→ 400 nested error。
  - GET /candles?datasetRef=sample → 200 / candles（int time）。
  - GET /candles?datasetRef=unknown → 400 nested error。
  - GET /（静的）→ 200 text/html（index.html）。
  - GET パストラバーサル → 404（web/ ルート外を配信しない）。

localhost のエフェメラルポートで起動し、urllib.request で叩く（stdlib のみ）。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from framework.server import IndicatorUIRequestHandler


@pytest.fixture()
def server():
    # エフェメラルポート（port=0）で localhost 起動。テスト後に確実に停止。
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


def _post_json(base: str, path: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(base: str, path: str):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


# --------------------------------------------------------------------------- #
# POST /compute
# --------------------------------------------------------------------------- #
def test_post_compute_returns_200_with_series(server):
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40, "q_low": 0.05, "q_high": 0.95},
        "datasetRef": "sample",
    }
    status, payload = _post_json(server, "/compute", body)
    assert status == 200
    assert payload["ok"] is True
    assert [s["name"] for s in payload["series"]] == ["btlm_mean", "btlm_q5", "btlm_q95"]


def test_post_compute_malformed_json_returns_400_nested_error(server):
    data = b"{not json"
    req = urllib.request.Request(
        server + "/compute", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, payload = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, json.loads(exc.read())
    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["type"] == "validation"


def test_post_compute_correlation_violation_returns_400(server):
    body = {
        "indicatorId": "tgp_btlm", "variant": "default",
        "params": {"fitter": "ols", "maxbars": 40, "q_low": 0.96, "q_high": 0.95},
        "datasetRef": "sample",
    }
    status, payload = _post_json(server, "/compute", body)
    assert status == 400
    assert payload["error"]["type"] == "validation"


# --------------------------------------------------------------------------- #
# GET /candles
# --------------------------------------------------------------------------- #
def test_get_candles_returns_200_with_int_time(server):
    status, ctype, raw = _get(server, "/candles?datasetRef=sample")
    assert status == 200
    assert "application/json" in ctype
    payload = json.loads(raw.decode("utf-8"))
    assert payload["ok"] is True
    assert isinstance(payload["candles"][0]["time"], int)


def test_get_candles_unknown_ref_returns_400(server):
    status, _ctype, raw = _get(server, "/candles?datasetRef=unknown")
    assert status == 400
    payload = json.loads(raw.decode("utf-8"))
    assert payload["error"]["type"] == "validation"


def test_get_candles_timeframe_and_limit_resamples_and_restricts(server):
    # timeframe=1W で週足へ resample、limit=3 で直近 3 本に制限（§チャート表示時間選択 / 配信設計）。
    status, _ctype, raw = _get(server, "/candles?datasetRef=sample&timeframe=1W&limit=3")
    assert status == 200
    payload = json.loads(raw.decode("utf-8"))
    assert payload["ok"] is True
    assert len(payload["candles"]) == 3
    assert isinstance(payload["candles"][0]["time"], int)


def test_get_candles_unknown_timeframe_returns_400(server):
    status, _ctype, raw = _get(server, "/candles?datasetRef=sample&timeframe=9z")
    assert status == 400
    payload = json.loads(raw.decode("utf-8"))
    assert payload["error"]["type"] == "validation"


# --------------------------------------------------------------------------- #
# GET /forming_bar（ライブ形成中バー・ティック由来）
# --------------------------------------------------------------------------- #
def test_get_forming_bar_unknown_ref_returns_400(server):
    status, _ctype, raw = _get(server, "/forming_bar?datasetRef=unknown&timeframe=1D")
    assert status == 400
    payload = json.loads(raw.decode("utf-8"))
    assert payload["error"]["type"] == "validation"


def test_get_forming_bar_unknown_timeframe_returns_400(server):
    status, _ctype, raw = _get(server, "/forming_bar?datasetRef=jp225_tick&timeframe=9z")
    assert status == 400
    payload = json.loads(raw.decode("utf-8"))
    assert payload["error"]["type"] == "validation"


def test_get_forming_bar_parquet_path_1W_is_null(server, monkeypatch):
    # parquet 経路（ロールアップ未成立時のフォールバック）では 1W は固定 floor 不可で非対応 → null。
    #   ※ ロールアップ方式では 1W も rollup partial から供給可（別テストで検証）。ここは fallback 層の不変。
    import framework.server as server_mod

    monkeypatch.setattr(server_mod.forming_bar_mod, "rollup_forming_bar", lambda *a, **k: None)  # 経路を強制
    status, _ctype, raw = _get(server, "/forming_bar?datasetRef=jp225_tick&timeframe=1W&now=1782505000")
    assert status == 200
    payload = json.loads(raw.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["bar"] is None


def test_get_forming_bar_returns_200_with_ok_and_bar_key(server):
    # now 注入で 200・{ok, bar} 形を固定（bar は実データ有無で object/null・形のみ検証）。
    status, _ctype, raw = _get(server, "/forming_bar?datasetRef=jp225_tick&timeframe=1D&now=1782505000")
    assert status == 200
    payload = json.loads(raw.decode("utf-8"))
    assert payload["ok"] is True
    assert "bar" in payload
    if payload["bar"] is not None:
        assert set(payload["bar"]) == {"time", "open", "high", "low", "close", "volume"}
        assert isinstance(payload["bar"]["time"], int)


def test_get_forming_bar_falls_back_to_live_buffer_when_parquet_window_empty(server, monkeypatch):
    # seed 鮮度化: parquet 経路が None（現周期窓が空）でも、注入 LiveTickBuffer に現周期 tick が
    #   あれば fallback で形成中バーを返す（短周期 1m/5m/15m のシード null 固着を防ぐ）。
    import framework.server as server_mod

    now = 1782505000                        # 実 UTC 現在を注入（now query）。
    start = 1782505000 - (1782505000 % 300)  # floor(now, 5m)。
    monkeypatch.setattr(server_mod.forming_bar_mod, "rollup_forming_bar", lambda *a, **k: None)  # 経路強制
    monkeypatch.setattr(server_mod.forming_bar_mod, "forming_bar", lambda *a, **k: None)  # parquet=空。

    class _FakeBuffer:
        def ticks_since(self, ms):
            return [[start * 1000 + 1000, 100.0], [start * 1000 + 2000, 105.0]]

    server_mod.set_live_tick_buffer(_FakeBuffer())
    try:
        status, _ctype, raw = _get(server, f"/forming_bar?datasetRef=jp225_tick&timeframe=5m&now={now}")
        assert status == 200
        payload = json.loads(raw.decode("utf-8"))
        assert payload["ok"] is True
        assert payload["bar"] is not None, "buffer fallback should supply a forming bar"
        assert payload["bar"]["time"] == start
        assert payload["bar"]["open"] == 100.0 and payload["bar"]["close"] == 105.0
    finally:
        server_mod.set_live_tick_buffer(None)


def test_get_forming_bar_uses_rollup_path_first(server, monkeypatch):
    # ロールアップ方式が優先: rollup_forming_bar が非 None を返せば parquet 経路は呼ばない（1W も可）。
    import framework.server as server_mod

    sentinel = {"time": 1782505000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 9.0}
    monkeypatch.setattr(server_mod.forming_bar_mod, "rollup_forming_bar", lambda *a, **k: sentinel)
    monkeypatch.setattr(server_mod.forming_bar_mod, "forming_bar",
                        lambda *a, **k: pytest.fail("rollup 経路成立時は parquet 経路を呼ばない"))
    status, _ctype, raw = _get(server, "/forming_bar?datasetRef=jp225_tick&timeframe=1W&now=1782505000")
    assert status == 200
    assert json.loads(raw.decode("utf-8"))["bar"] == sentinel


def test_get_forming_bar_falls_back_when_rollup_path_none(server, monkeypatch):
    # ロールアップ経路が None（未成立）なら現行 parquet 経路へフォールバック（非破壊）。
    import framework.server as server_mod

    fallback = {"time": 1782505000, "open": 3.0, "high": 4.0, "low": 2.0, "close": 3.5, "volume": 1.0}
    monkeypatch.setattr(server_mod.forming_bar_mod, "rollup_forming_bar", lambda *a, **k: None)
    monkeypatch.setattr(server_mod.forming_bar_mod, "forming_bar", lambda *a, **k: fallback)
    status, _ctype, raw = _get(server, "/forming_bar?datasetRef=jp225_tick&timeframe=5m&now=1782505000")
    assert status == 200
    assert json.loads(raw.decode("utf-8"))["bar"] == fallback


def test_get_forming_bar_fallback_unsupported_tf_ignores_live_buffer(server, monkeypatch):
    # フォールバック経路（ロールアップ未成立時）は非対応 tf（1W）で buffer を参照せず null。
    import framework.server as server_mod

    monkeypatch.setattr(server_mod.forming_bar_mod, "rollup_forming_bar", lambda *a, **k: None)  # 経路を強制
    monkeypatch.setattr(server_mod.forming_bar_mod, "forming_bar", lambda *a, **k: None)

    class _FakeBuffer:
        def ticks_since(self, ms):
            raise AssertionError("非対応 tf のフォールバックでは buffer を参照してはいけない")

    server_mod.set_live_tick_buffer(_FakeBuffer())
    try:
        status, _ctype, raw = _get(server, "/forming_bar?datasetRef=jp225_tick&timeframe=1W&now=1782505000")
        assert status == 200
        assert json.loads(raw.decode("utf-8"))["bar"] is None
    finally:
        server_mod.set_live_tick_buffer(None)


# --------------------------------------------------------------------------- #
# GET /live_ticks（ライブ tick バッファ配信・ISSUE-049）
#   殻は注入された LiveTickBuffer.ticks_since(since) を JSON 応答へ載せる。buffer 未注入
#   （テスト既定・自動起動なし）は空 ticks を返す（記録系・ネットワーク非依存）。
# --------------------------------------------------------------------------- #
def test_get_live_ticks_returns_empty_when_no_buffer_injected(server):
    # 既定（buffer 未注入）は ok・空 ticks・serverNowMs を返す（fetch を起動しない）。
    from framework import server as server_mod

    server_mod.set_live_tick_buffer(None)
    status, ctype, raw = _get(server, "/live_ticks?since=0")
    assert status == 200
    assert "application/json" in ctype
    payload = json.loads(raw.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["ticks"] == []
    assert isinstance(payload["serverNowMs"], int)


def test_get_live_ticks_serves_injected_buffer_since_cursor(server):
    # フェイク buffer を注入 → ticks_since(since) の戻りをそのまま [[ms, mid], ...] で返す。
    from framework import server as server_mod

    class _FakeBuffer:
        def __init__(self):
            self.seen = []

        def ticks_since(self, ms):
            self.seen.append(ms)
            return [[1000, 39005.0], [1500, 39007.0]]

    fake = _FakeBuffer()
    server_mod.set_live_tick_buffer(fake)
    try:
        status, _ctype, raw = _get(server, "/live_ticks?since=999")
        assert status == 200
        payload = json.loads(raw.decode("utf-8"))
        assert payload["ok"] is True
        assert payload["ticks"] == [[1000, 39005.0], [1500, 39007.0]]
        assert fake.seen[-1] == 999
    finally:
        server_mod.set_live_tick_buffer(None)


# --------------------------------------------------------------------------- #
# /market_profile_forming 殻の from 透過（回帰: 当日窓へレンジを絞る根幹）
# --------------------------------------------------------------------------- #
def test_get_market_profile_forming_passes_from_to_controller(server, monkeypatch):
    """殻 `_compute_market_profile_forming` が query の `from` を controller へ `frm` として渡す。

    これが欠けると controller の from_ts=None に落ち、base レンジが全期間 low/high へ広がり
    当日成長が不可視になる（兄弟 `_compute_market_profile` と同型の透過を固定する回帰）。
    """
    import framework.server as _srv

    captured = {}

    def _spy(ref, timeframe, since, base, now, bins, va, barw, frm=None):
        captured["frm"] = frm
        return 200, {"ok": True, "frm_echo": frm}

    monkeypatch.setattr(_srv, "handle_market_profile_forming", _spy)
    status, _ctype, raw = _get(
        server,
        "/market_profile_forming?datasetRef=jp225_tick&timeframe=1D&base=1&from=1783382400&bins=60",
    )
    assert status == 200
    assert captured.get("frm") == "1783382400"  # query の from が frm として届く。


def test_get_tf_period_profile_passes_window_to_controller(server, monkeypatch):
    # 殻 `_compute_tf_period_profile` が query の from/to（ローリング窓）を controller へ透過し JSON 応答する。
    import framework.server as _srv

    captured = {}

    def _spy(ref, timeframe, frm, to, src=None, live_ticks=None, va=None):
        captured.update(ref=ref, tf=timeframe, frm=frm, to=to, src=src,
                        live_ticks=live_ticks, va=va)
        return 200, {"ok": True, "tf": timeframe, "columns": []}

    monkeypatch.setattr(_srv, "handle_tf_period_profile", _spy)
    status, _ctype, raw = _get(
        server, "/tf_period_profile?datasetRef=jp225_tick&timeframe=5m&from=1000&to=2000")
    assert status == 200
    payload = json.loads(raw.decode("utf-8"))
    assert payload["ok"] is True and payload["tf"] == "5m"
    assert (captured["frm"], captured["to"]) == ("1000", "2000")
    assert captured["src"] is None  # src 省略時は None（従来経路・byte 不変）
    assert captured["live_ticks"] is None  # buffer 未注入時は None（従来経路・byte 不変）

    assert captured["va"] is None  # va 省略時は None（controller が既定へ解決＝byte 不変）

    # src=zp はそのまま controller へ透過される。
    status, _ctype, _raw = _get(
        server, "/tf_period_profile?datasetRef=jp225_tick&timeframe=1h&from=1000&to=2000&src=zp")
    assert status == 200
    assert captured["src"] == "zp"

    # ISSUE-260: va（バリューエリア比率）も殻がそのまま controller へ透過する。
    #   ここが欠けると UI の設定が日別プロファイル列に届かない（効かないツマミ）。
    status, _ctype, _raw = _get(
        server,
        "/tf_period_profile?datasetRef=jp225_tick&timeframe=5m&from=1000&to=2000&va=0.55")
    assert status == 200
    assert captured["va"] == "0.55"


def test_get_tf_period_profile_passes_live_buffer_ticks(server, monkeypatch):
    # ISSUE-083 追補: 注入 LiveTickBuffer の末尾 (ms, mid) が live_ticks として controller へ渡る
    #   （当日列の最新化）。非 tick ref（バリデーションで 400 だが殻は buffer を読まない）は None。
    import framework.server as server_mod

    captured = {}

    def _spy(ref, timeframe, frm, to, src=None, live_ticks=None, va=None):
        captured.update(live_ticks=live_ticks)
        return 200, {"ok": True, "tf": timeframe, "columns": []}

    monkeypatch.setattr(server_mod, "handle_tf_period_profile", _spy)

    class _FakeBuffer:
        def ticks_since(self, ms):
            return [[1783382401000, 100.0], [1783382402000, 105.0]]

    server_mod.set_live_tick_buffer(_FakeBuffer())
    try:
        status, _ctype, _raw = _get(
            server, "/tf_period_profile?datasetRef=jp225_tick&timeframe=5m&from=1000&to=2000")
        assert status == 200
        assert captured["live_ticks"] == [[1783382401000, 100.0], [1783382402000, 105.0]]
    finally:
        server_mod.set_live_tick_buffer(None)


def test_get_market_profile_forming_augments_ticks_with_live_buffer(server, monkeypatch):
    # 秒成長の遅延解消: controller の forming ticks（parquet 由来・フロンティア遅延で末尾欠け）を、
    #   注入 LiveTickBuffer の「parquet 末尾より後」の tick で補完して返す（非破壊・重複なし）。
    import framework.server as server_mod

    fs, now = 1783382400, 1783382445  # formingStart / now（45s 経過）。
    # parquet forming ticks はフロンティア遅延で fs+10 までしか無い。
    def _spy(ref, timeframe, since, base, now_ov, bins, va, barw, frm=None):
        return 200, {"ok": True, "formingStart": fs, "now": now,
                     "ticks": [[fs + 5, 100.0], [fs + 10, 101.0]]}

    monkeypatch.setattr(server_mod, "handle_market_profile_forming", _spy)

    class _FakeBuffer:  # fs+10 より後（fs+30/40）を near-real-time で保持。
        def ticks_since(self, ms):
            return [[(fs + 5) * 1000, 100.0], [(fs + 30) * 1000, 103.0], [(fs + 40) * 1000, 104.0]]

    server_mod.set_live_tick_buffer(_FakeBuffer())
    try:
        status, _ctype, raw = _get(
            server, f"/market_profile_forming?datasetRef=jp225_tick&timeframe=1m&base=1&now={now}")
        assert status == 200
        payload = json.loads(raw.decode("utf-8"))
        # parquet 分（fs+5, fs+10）＋ buffer の parquet 末尾より後（fs+30, fs+40）。fs+5 の重複は載せない。
        assert payload["ticks"] == [[fs + 5, 100.0], [fs + 10, 101.0], [fs + 30, 103.0], [fs + 40, 104.0]]
    finally:
        server_mod.set_live_tick_buffer(None)


# --------------------------------------------------------------------------- #
# 静的配信 / パストラバーサル
# --------------------------------------------------------------------------- #
def test_get_root_serves_index_html(server):
    status, ctype, raw = _get(server, "/")
    assert status == 200
    assert "text/html" in ctype
    assert b"<!DOCTYPE html>" in raw[:200] or b"<!doctype html>" in raw[:200].lower()


def test_get_static_module_serves_javascript(server):
    status, ctype, _raw = _get(server, "/js/adapter/front/composition_root_front.js")
    assert status == 200
    assert "javascript" in ctype


def test_get_path_traversal_is_rejected_404(server):
    # web/ ルート外（../../ で抜ける）は配信しない。
    status, _ctype, _raw = _get(server, "/../../../../etc/passwd")
    assert status == 404


# --------------------------------------------------------------------------- #
# GET /catalog（param 既定値の単一情報源・ISSUE-092 ③）
# --------------------------------------------------------------------------- #
def test_get_catalog_returns_200_with_single_source_schema(server):
    # 殻が /catalog を handle_catalog へ配線し、正典 ok 形で param 既定値スキーマを返す。
    from adapter.compute.catalog_schema import PARAM_DEFAULTS

    status, ctype, raw = _get(server, "/catalog")
    assert status == 200
    assert "application/json" in ctype
    payload = json.loads(raw.decode("utf-8"))
    assert payload["ok"] is True
    # 配信スキーマは back single source と一致（front はこれを overlay で解決）。
    assert payload["catalog"] == PARAM_DEFAULTS
