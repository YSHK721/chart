"""実 HTTP 供給元の検定（ISSUE-447 段階 1 / 設計 §4 wire 契約・E-7・E-8）。

実 HTTP を張る理由:
    ``wire`` の往復は既に純関数として検定してある。ここで確かめたいのはその先——
    **ソケットとヘッダを通しても契約が保たれるか**である。署名は正準文字列がバイト列として
    一致して初めて通り、body は Content-Length と dtype の整合が取れて初めて解ける。
    fake を挟むとその 2 つが検定から消える。

外部ネットワークには一切出ない（127.0.0.1 の一時ポートに stdlib の ``http.server`` を立て、
VM 側の要求処理 ``feed.handle_request`` をそのまま結線する）。実端末
（172.16.162.129）へは接続しない。MetaTrader5 も import しない（端末は fake）。
"""
from __future__ import annotations

import threading
from http.server import HTTPServer
from types import SimpleNamespace

import numpy as np
import pytest

from marketdata.mt5_ticks import http_source, wire
from marketdata.mt5_ticks.port import Mt5SupplyError, SupplyUnavailable
from tools import mt5_tick_feed as feed

_SECRET = b"integration-test-secret"
_KEY_ID = "k1"
_SERVER_NAME = "OANDA-Japan MT5 Live"

_MT5_TICK_DTYPE = np.dtype([
    ("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"),
    ("volume", "<u8"), ("time_msc", "<i8"), ("flags", "<u4"), ("volume_real", "<f8"),
])


def _ticks(*specs) -> np.ndarray:
    arr = np.zeros(len(specs), dtype=_MT5_TICK_DTYPE)
    for i, (msc, bid, ask) in enumerate(specs):
        arr[i]["time"] = msc // 1000
        arr[i]["time_msc"] = msc
        arr[i]["bid"] = bid
        arr[i]["ask"] = ask
    return arr


class _FakeMt5:
    """端末の代役（読み取りのみ・MetaTrader5 に依存しない）。"""

    COPY_TICKS_INFO = 1

    def __init__(self, ticks=None, *, select_ok=True, error=(-10005, "no ipc connection")):
        self._ticks = ticks
        self._select_ok = select_ok
        self._error = error

    def symbol_select(self, symbol, enable=True):
        return self._select_ok

    def copy_ticks_from(self, symbol, frm, count, flags):
        return None if self._ticks is None else self._ticks[:count]

    def copy_ticks_range(self, symbol, frm, to, flags):
        return self._ticks

    def last_error(self):
        return self._error

    def account_info(self):
        return SimpleNamespace(server=_SERVER_NAME)


class _Endpoint:
    """一時ポートで動く VM 側 feed（テストの終わりに必ず閉じる）。"""

    def __init__(self, mt5, *, secret=_SECRET, key_id=_KEY_ID):
        handler = feed.make_handler(
            mt5=mt5, secret=secret, key_id=key_id, nonces=feed.NonceCache()
        )
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture()
def endpoint():
    made: "list[_Endpoint]" = []

    def _make(mt5, **kwargs):
        made.append(_Endpoint(mt5, **kwargs))
        return made[-1]

    yield _make
    for one in made:
        one.close()


def _source(url, *, secret=_SECRET, key_id=_KEY_ID, **kwargs):
    return http_source.HttpTickSource(url, key_id=key_id, secret=secret, **kwargs)


# =====================================================================
# 実 HTTP 往復（N-6 の端から端）
# =====================================================================

def test_a_signed_request_comes_back_as_parsed_rows(endpoint):
    """署名 → HTTP → 生 body → 解析まで通しで成立する。"""
    at = endpoint(_FakeMt5(_ticks((1000, 66020.1, 66035.1), (1001, 66020.2, 66035.2))))

    got = _source(at.url).fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)

    assert got.rows == [(1000, 66020.1, 66035.1), (1001, 66020.2, 66035.2)]
    assert got.latest_msc == 1001
    assert got.server == _SERVER_NAME


def test_an_empty_window_is_a_normal_answer_not_an_error(endpoint):
    """B-4(1): 0 行は正常応答（黙って例外にしない・待てば来る）。"""
    at = endpoint(_FakeMt5(_ticks()))

    got = _source(at.url).fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)

    assert got.rows == []


def test_the_upper_bound_of_the_window_is_carried_over_the_wire(endpoint):
    """上端付きの窓が要求として伝わる（上端を落とすと取り過ぎる）。"""
    at = endpoint(_FakeMt5(_ticks((1000, 1.0, 2.0), (1500, 1.0, 2.0))))

    got = _source(at.url).fetch(symbol="JP225", from_msc=1000, to_msc=2000, max_rows=10)

    assert [r[0] for r in got.rows] == [1000, 1500]


def test_a_truncated_answer_is_reported_so_the_caller_can_keep_pulling(endpoint):
    """B-4(2): 切り詰めが呼び出し側へ伝わる。"""
    at = endpoint(_FakeMt5(_ticks(*[(1000 + i, 1.0, 2.0) for i in range(12)])))

    got = _source(at.url).fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=5)

    assert got.truncated is True
    assert len(got.rows) == 5


# =====================================================================
# E-7 / E-8 / 引数不正 — 再試行してよい障害と、してはいけない障害を分ける
# =====================================================================

def test_a_bad_key_is_a_retryable_supply_failure(endpoint):
    """E-7: 鍵不一致 → 401 → :class:`SupplyUnavailable`（バックオフ対象）。"""
    at = endpoint(_FakeMt5(_ticks()), secret=b"the-server-has-another-secret")

    with pytest.raises(SupplyUnavailable):
        _source(at.url).fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)


def test_a_replayed_nonce_is_refused_by_the_server(endpoint):
    """E-7: nonce は毎回変わる。同じ nonce を返す供給元は 2 回目で 401 になる。"""
    at = endpoint(_FakeMt5(_ticks()))
    source = _source(at.url, nonce_factory=lambda: "always-the-same")

    source.fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)
    with pytest.raises(SupplyUnavailable):
        source.fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)


def test_the_default_nonce_never_repeats_itself(endpoint):
    """既定の nonce は毎回異なる（再生防止が既定で効く）。

    2 回とも通ること自体が主張である。VM 側は使用済み nonce を拒むため、既定が固定値なら
    2 回目が :class:`SupplyUnavailable` になる。
    """
    at = endpoint(_FakeMt5(_ticks()))
    source = _source(at.url)

    first = source.fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)
    second = source.fetch(symbol="JP225", from_msc=1001, to_msc=None, max_rows=10)

    assert (first.rows, second.rows) == ([], [])


def test_a_terminal_failure_is_retryable_and_keeps_the_last_error(endpoint):
    """E-8: 端末が None → 502 → 再試行可・``last_error`` が失われない。"""
    at = endpoint(_FakeMt5(None, error=(-10005, "no ipc connection")))

    with pytest.raises(SupplyUnavailable) as caught:
        _source(at.url).fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)

    assert "no ipc connection" in str(caught.value)


def test_a_bad_argument_is_fail_stop_rather_than_retried(endpoint):
    """400 は待っても直らない。再試行対象にしない（投げ続けない）。"""
    at = endpoint(_FakeMt5(_ticks()))

    with pytest.raises(Mt5SupplyError) as caught:
        _source(at.url).fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=0)

    assert not isinstance(caught.value, SupplyUnavailable)


def test_an_unreachable_endpoint_is_a_retryable_supply_failure():
    """接続できない（VM 停止・経路断）も待てば直りうる障害である。"""
    source = _source("http://127.0.0.1:9", timeout=0.5)

    with pytest.raises(SupplyUnavailable):
        source.fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)


# =====================================================================
# 転送そのものの安全側（timeout 必須・リダイレクト不追従・上限）
# =====================================================================

def test_a_timeout_is_always_attached_to_the_request(endpoint, monkeypatch):
    """timeout 無しの要求を作れない（応答が来ない相手に常駐が張り付くのを構造で防ぐ）。"""
    at = endpoint(_FakeMt5(_ticks()))
    seen: "list[object]" = []
    original = http_source.request.OpenerDirector.open

    def _record(self, fullurl, data=None, timeout=None):
        seen.append(timeout)
        return original(self, fullurl, data, timeout)

    monkeypatch.setattr(http_source.request.OpenerDirector, "open", _record)
    _source(at.url, timeout=3.5).fetch(symbol="JP225", from_msc=1, to_msc=None, max_rows=10)

    assert seen == [3.5]


@pytest.mark.parametrize("bad", [0, -1, None])
def test_a_source_without_a_positive_timeout_is_refused(bad):
    """timeout を外す・0 にする経路を作らない（既定へ黙って戻さない）。"""
    with pytest.raises(ValueError):
        _source("http://127.0.0.1:1", timeout=bad)


def test_redirects_are_not_followed(monkeypatch):
    """リダイレクトを追わない（供給元の差し替えを応答側に握らせない）。"""
    server = HTTPServer(("127.0.0.1", 0), _RedirectingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with pytest.raises(Mt5SupplyError) as caught:
            _source(f"http://{host}:{port}").fetch(
                symbol="JP225", from_msc=1, to_msc=None, max_rows=10
            )
        assert not isinstance(caught.value, SupplyUnavailable)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_an_oversized_answer_is_refused_before_it_is_read(endpoint):
    """Content-Length 上限を超える応答は body を読まずに拒む（メモリを預けない）。"""
    at = endpoint(_FakeMt5(_ticks(*[(1000 + i, 1.0, 2.0) for i in range(50)])))

    with pytest.raises(Mt5SupplyError):
        _source(at.url, max_bytes=16).fetch(
            symbol="JP225", from_msc=1000, to_msc=None, max_rows=50
        )


def test_the_limit_leaves_a_normal_answer_alone(endpoint):
    """上限は異常だけを切る（正常な応答の大きさで誤爆しない）。"""
    at = endpoint(_FakeMt5(_ticks((1000, 1.0, 2.0))))

    got = _source(at.url, max_bytes=1024).fetch(
        symbol="JP225", from_msc=1000, to_msc=None, max_rows=10
    )

    assert len(got.rows) == 1


# =====================================================================
# 計算量: 1 回の fetch は 1 回の要求（先読み・取り直しを持たない）
# =====================================================================

@pytest.mark.parametrize("from_msc", [1_000, 1_700_000_000_000])
def test_one_fetch_issues_exactly_one_request(endpoint, monkeypatch, from_msc):
    """CX-c: 発行する要求はカーソル位置に依存しない（2 点で「増えない」を固定）。

    固定するのは回数そのものではなく、**出力に使わない要求が 0 である**ことである。
    """
    at = endpoint(_FakeMt5(_ticks((from_msc, 1.0, 2.0))))
    issued: "list[str]" = []
    original = http_source.request.OpenerDirector.open

    def _record(self, fullurl, data=None, timeout=None):
        issued.append(getattr(fullurl, "full_url", str(fullurl)))
        return original(self, fullurl, data, timeout)

    monkeypatch.setattr(http_source.request.OpenerDirector, "open", _record)
    got = _source(at.url).fetch(symbol="JP225", from_msc=from_msc, to_msc=None, max_rows=10)

    assert len(issued) == len(got.rows) == 1


def test_the_source_satisfies_the_incremental_tick_source_port():
    """DIP の適用点に嵌まる（fake / spy と差し替え可能である）。"""
    from marketdata.mt5_ticks.port import IncrementalTickSource

    assert isinstance(_source("http://127.0.0.1:1"), IncrementalTickSource)


def test_the_query_is_the_one_the_wire_contract_defines():
    """要求クエリの組み立ては ``wire`` に委譲する（第 2 定義を作らない）。"""
    assert http_source.wire.build_query is wire.build_query


class _RedirectingHandler(feed.BaseHTTPRequestHandler):
    """常に 302 を返すだけの相手（リダイレクト不追従の検定に使う）。"""

    def do_GET(self):  # noqa: N802  （BaseHTTPRequestHandler の規約）
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/elsewhere")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass
