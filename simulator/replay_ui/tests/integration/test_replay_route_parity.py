"""分割前 golden: replay の全ルート応答を byte 単位で凍結する（ISSUE-479 Wave2 3-3〜3-5）。

なぜ先に凍結するか:
    serve_replay の Handler を機能別 App へ委譲分割する。分割は**応答を 1 バイトも変えない**
    ことが条件なので、変える前に「今どう応答しているか」を機械可読な形で固定しておく。
    分割後に同じ検定が緑であることが、パリティの証拠になる。

凍結する内容（1 ケースにつき 1 ダイジェスト）:
    ステータス行 ＋ ヘッダ集合（Date / Server を除く名前と値）＋ ボディ全 byte の sha256。
    ダイジェストにするのは、ヘッダ 1 つの増減もボディ 1 バイトの差も等しく落とすため。
    診断のためステータスとボディ長も併記し、失敗時にどこがずれたかが読めるようにする。

対象は 10 エンドポイント（GET 7・POST /compute の 3 モード）を正常系と異常系で。
異常系には validation（400）と internal（500）の双方を含める——例外分類の表化
（3-5）で最も壊れやすいのがこの 2 つの経路だからである。

計算量検定（絶対命令 2026-08-28）: 1 リクエストにつき重い処理の実行は 1 回
    （発行 − 応答に使った計算 = 0）。ルート数を変えた 2 点で、1 リクエストあたりの
    発行が増えないことを固定する（二重 dispatch の不在）。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
from pathlib import Path

import pytest

from simulator.replay_ui.framework.serve_replay import ReplayApp, make_server

BARS = [{"time": 60, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"time": 120, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0}]

class Candle:
    def load_candles(self, ref, tf, limit): return [dict(b) for b in BARS]
    def load_candles_from(self, ref, tf, start, pre, limit): return [dict(b) for b in BARS]
class Compute:
    def load_source(self, ref, tf):
        if ref == "boom": raise ValueError("unknown ref")
        if ref == "kaboom": raise RuntimeError("boom inside")
        return [dict(b) for b in BARS]
    def bar_time(self, tf, s): return int(s)
    def period_start(self, tf, s): return int(s)
    def causal_series(self, *a): return []
    def compute(self, ind, var, mode, bars, params): return [{"name": ind, "data": [{"time": 60, "value": 1.0}]}]
    def compute_latest_seq(self, ind, var, prefix, tails, params): return [[{"name": ind}] for _ in tails]
class Window:
    def load_m1_rows(self, ref, start, end): return [[1.0, 2.0, 0.5, 1.5]]
    def load_raw_ticks(self, start, end): return [(60, 1.0, 1.1)]
class Days:
    def load_days(self, ref, tf): return ["2026-01-01"]
class Forming:
    def forming(self, *a, **k): return (200, {"ok": True, "forming": []})
class MP:
    def profile(self, *a, **k): return (200, {"ok": True, "profile": []})
class TV:
    def profile(self, *a, **k): return (200, {"ok": True, "bands": []})
class Cat:
    def catalog(self): return (200, {"ok": True, "catalog": {}})

def build(web_dir, **over):
    kw = dict(candle_port=Candle(), compute_port=Compute(), window_port=Window(),
              is_known_ref=lambda r: r != "nope", web_dir=web_dir,
              days_port=Days(), forming_port=Forming(), market_profile_port=MP(),
              tickvol_profile_port=TV(), catalog_port=Cat())
    kw.update(over)
    return ReplayApp(**kw)

CASES = [
    ("candles_ok", "GET", "/candles?datasetRef=jp225_m1&timeframe=5m&limit=2", None),
    ("candles_bad", "GET", "/candles?datasetRef=jp225_m1&from=xx", None),
    ("available_days_ok", "GET", "/available_days?datasetRef=jp225_m1", None),
    ("intraday_ok", "GET", "/intraday?datasetRef=jp225_m1&start=60&end=120", None),
    ("intraday_bad", "GET", "/intraday?datasetRef=jp225_m1&start=zz", None),
    ("catalog_ok", "GET", "/catalog", None),
    ("tickvol_ok", "GET", "/tickvol_profile?datasetRef=jp225_tick", None),
    ("market_profile_ok", "GET", "/market_profile?datasetRef=jp225_tick&timeframe=5m", None),
    ("mp_forming_ok", "GET", "/market_profile_forming?datasetRef=jp225_tick&now=60", None),
    ("static_missing", "GET", "/no-such-asset.js", None),
    ("compute_ok", "POST", "/compute", {"indicatorId": "ma", "variant": "d",
                                        "datasetRef": "jp225_m1", "timeframe": "5m",
                                        "params": {}, "generation": 3}),
    ("compute_value_error", "POST", "/compute", {"indicatorId": "ma", "variant": "d",
                                                 "datasetRef": "boom", "timeframe": "5m",
                                                 "params": {}, "generation": 4}),
    ("compute_internal_error", "POST", "/compute", {"indicatorId": "ma", "variant": "d",
                                                    "datasetRef": "kaboom", "timeframe": "5m",
                                                    "params": {}, "generation": 9}),
    ("compute_seq_ok", "POST", "/compute", {"mode": "latest_seq", "indicatorId": "ma",
                                            "variant": "d", "datasetRef": "jp225_m1",
                                            "timeframe": "5m", "params": {},
                                            "formingSeq": [{"time": 120, "open": 1.0, "high": 1.0,
                                                            "low": 1.0, "close": 1.0}],
                                            "generation": 5}),
    ("compute_seq_multi_ok", "POST", "/compute", {"mode": "latest_seq_multi",
                                                  "datasetRef": "jp225_m1", "timeframe": "5m",
                                                  "formingSeq": [{"time": 120, "open": 1.0,
                                                                  "high": 1.0, "low": 1.0,
                                                                  "close": 1.0}],
                                                  "specs": [{"instanceId": "i1", "indicatorId": "ma",
                                                             "variant": "d", "params": {}}],
                                                  "generation": 6}),
    ("post_not_compute", "POST", "/other", {}),
]

def capture(app):
    server = make_server(app, port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    port = server.server_address[1]
    out = {}
    try:
        for name, method, path, body in CASES:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            r = conn.getresponse(); data = r.read()
            hdrs = tuple(sorted((k, v) for k, v in r.getheaders() if k.lower() not in ("date", "server")))
            out[name] = (r.status, hdrs, data)
            conn.close()
    finally:
        server.shutdown(); server.server_close()
    return out



#: 分割前の実測（2026-09-03）。値は「ダイジェスト・ステータス・ボディ長」。
#: 分割でこの表が 1 件でも動いたら、それは応答が変わったということである。
_GOLDEN = {
    "candles_ok": ("6d3579d7c78b43c020556ea36f4441596fd7f62e8ee10e1a8b10d781e97a8d89", 200, 158),
    "candles_bad": ("2ff7ed187c2bf81cbc9dc1970c9d3e79bc4ae1dde2ed0ffd4ceb29a68b1d952c", 400, 116),
    "available_days_ok": ("8d99aab207af254555bd080cf291334b9265fbed0e01600bdf5df7f6dd709d1a", 200, 36),
    "intraday_ok": ("d835755aff1bb2a1642daa9195c4b5e19a5488649de83599aa055ff5eea206b7", 200, 59),
    "intraday_bad": ("d5987814d108226f6ba94d5d59d22171a78fce42fdc1295edf1bbed5fe5041dd", 400, 114),
    "catalog_ok": ("2d8ebb2ca888958babb5bcf65264524e4f5251489379ebfc3cd93f1cdadd91e3", 200, 27),
    "tickvol_ok": ("dc63cc5ab63e3b16ec9a455670608bd83e30c4ca4f7021b76265833552bb6a09", 200, 25),
    "market_profile_ok": ("7f2ab3c6713d6bee551ffe73246cb2d3f0756dbe5c298b62c07cf64e9b9aae2a", 200, 27),
    "mp_forming_ok": ("efee4fed68bb873ec03674b14b0ba086ef4bacf7565c34938a477d9db25a7ed0", 200, 27),
    "static_missing": ("fb287dcae11addaa798a9b30b0656e25e9ec5cd9aab0a5298fb9e0c37f7b53eb", 404, 0),
    "compute_ok": ("6e785fb6c895ac164f5b8c56f4c2b97355ec450c21f52a62d5f713efb283192a", 200, 95),
    "compute_value_error": ("a3766bf351b49804c0c4167a21033a914cd247da1ee2d82f0089767393877265", 400, 107),
    "compute_internal_error": ("f39f52db0c7b690d28c3d99e70f1206e3b3d9bc7fc583146cbe82013b307643f", 500, 119),
    "compute_seq_ok": ("b8d56e9f0023782a43eb223098e560e156cfe08126ffdb89300cf9359c066fec", 200, 58),
    "compute_seq_multi_ok": ("1fbf342b75da646b2ccc1f9c9e4857ee619efa61478fd0c1d6031e4f6d16d258", 200, 68),
    "post_not_compute": ("fb287dcae11addaa798a9b30b0656e25e9ec5cd9aab0a5298fb9e0c37f7b53eb", 404, 0),
}


def _digest(status, hdrs, body) -> str:
    return hashlib.sha256(
        (str(status) + "\n" + repr(hdrs) + "\n").encode() + body
    ).hexdigest()


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    """全ルートの応答を 1 回だけ採取する（1 ケース 1 リクエスト）。"""
    web = tmp_path_factory.mktemp("web")
    return capture(build(web))


def test_the_case_table_covers_every_route() -> None:
    """走査が痩せていないこと（ゲートの自己検査）。"""
    covered = {path.split("?")[0] for _n, _m, path, _b in CASES}
    for route in ("/candles", "/available_days", "/intraday", "/catalog",
                  "/tickvol_profile", "/market_profile", "/market_profile_forming",
                  "/compute"):
        assert route in covered, route
    assert set(_GOLDEN) == {name for name, *_ in CASES}


@pytest.mark.parametrize("name", sorted(_GOLDEN))
def test_the_response_matches_the_frozen_golden(captured, name: str) -> None:
    """ステータス・ヘッダ集合・ボディ byte が分割前と完全一致する。"""
    status, hdrs, body = captured[name]
    want_digest, want_status, want_len = _GOLDEN[name]
    assert (status, len(body)) == (want_status, want_len), (name, hdrs, body[:300])
    assert _digest(status, hdrs, body) == want_digest, (name, status, hdrs, body[:300])


def test_the_golden_is_sensitive_to_a_missing_route(tmp_path) -> None:
    """検出力: ルートを 1 つ落とすと、その経路の応答は golden と一致しなくなる。

    分割で prefix を 1 つ配り忘れたときに気づけるかを、実際に落として確かめる。
    """
    app = build(tmp_path / "web2", catalog_port=None)
    got = capture(app)
    status, hdrs, body = got["catalog_ok"]
    assert _digest(status, hdrs, body) != _GOLDEN["catalog_ok"][0]
    assert status == 404, (status, body[:200])


class _CountingCompute(Compute):
    """重い経路（指標計算）の発行だけを数える Spy。"""

    def __init__(self) -> None:
        self.computed = 0

    def compute(self, ind, var, mode, bars, params):
        self.computed += 1
        return super().compute(ind, var, mode, bars, params)


def _post_compute_n(app, times: int) -> None:
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    payload = json.dumps({
        "indicatorId": "ma", "variant": "d", "datasetRef": "jp225_m1",
        "timeframe": "5m", "params": {}, "generation": 1,
    }).encode()
    try:
        for _ in range(times):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            conn.request("POST", "/compute", body=payload,
                         headers={"Content-Type": "application/json"})
            conn.getresponse().read()
            conn.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("requests_made", [1, 4], ids=["req_1", "req_4"])
def test_each_request_runs_the_heavy_work_once(tmp_path, requests_made: int) -> None:
    """リクエスト 1 回 / 4 回の 2 点で「重い計算の発行 − リクエスト数 = 0」。

    二重 dispatch（同じリクエストを 2 つの App が拾って両方が計算する）を禁じる。
    回数リテラルは焼き込まず、リクエスト数から導出する。
    """
    # Arrange
    spy = _CountingCompute()
    app = build(tmp_path / f"web_{requests_made}", compute_port=spy)
    # Act
    _post_compute_n(app, requests_made)
    # Assert
    assert spy.computed - requests_made == 0, spy.computed


def test_the_heavy_issue_count_does_not_grow_with_the_number_of_routes(tmp_path) -> None:
    """オーダーの表明: 有効ルート数を変えても 1 リクエストあたりの発行は変わらない。"""
    # Arrange / Act
    measured = {}
    for label, overrides in (
        ("few", {"days_port": None, "catalog_port": None, "tickvol_profile_port": None}),
        ("all", {}),
    ):
        spy = _CountingCompute()
        app = build(tmp_path / f"web_{label}", compute_port=spy, **overrides)
        _post_compute_n(app, 2)
        measured[label] = spy.computed
    # Assert
    assert measured["few"] == measured["all"], measured
