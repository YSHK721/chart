"""``IncrementalTickSource`` の**共有契約検定**（実 HTTP / Fake / Spy を同じ検定に通す）。

なぜ ``isinstance`` では足りないか:
    :class:`marketdata.mt5_ticks.port.IncrementalTickSource` は ``runtime_checkable`` な
    Protocol であり、``isinstance`` が見るのは **``fetch`` という名前が在ること**だけである
    （実測: 引数がまるで違う実装も、失敗を ``None`` で表す実装も通過する）。差し替え可能性は
    名前ではなく振る舞いで決まるのだから、境界の検定も振る舞いで書く。既存の isinstance 検定
    （``test_mt5_port.py``）は「名前が在る」ことの検定として残し、本ファイルがその隙間を埋める。

3 実装すべてに課す 4 点:
    1. キーワード専用の 4 引数（``symbol`` / ``from_msc`` / ``to_msc`` / ``max_rows``）を受ける
    2. 0 行の応答で ``latest_msc == from_msc``（次の窓の下端が後退しない＝取りこぼさない）
    3. 供給できないときは**例外**で表す（``None`` や空応答で表さない）
    4. ``truncated`` が「返せる行が ``max_rows`` を超えた」ことと一致する

実 HTTP の実装は 127.0.0.1 の一時ポートに stdlib の ``http.server`` を立てて通す
（外部ネットワークにも実端末にも出ない）。MetaTrader5 は import しない。
"""
from __future__ import annotations

import inspect
import threading
from contextlib import contextmanager
from http.server import HTTPServer
from types import SimpleNamespace

import numpy as np
import pytest

from marketdata.mt5_ticks import fakes, http_source, port
from marketdata.mt5_ticks.port import Mt5SupplyError, SupplyUnavailable
from tools import mt5_tick_feed as feed

_SECRET = b"contract-test-secret"
_KEY_ID = "k1"
_SERVER_NAME = "OANDA-Japan MT5 Live"

#: MT5 が返す構造化配列の型（**ライブラリ側の定義**であって本リポジトリの定義ではない）。
_MT5_TICK_DTYPE = np.dtype([
    ("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"),
    ("volume", "<u8"), ("time_msc", "<i8"), ("flags", "<u4"), ("volume_real", "<f8"),
])

_TAPE = [(1000, 66020.1, 66035.1), (1001, 66020.2, 66035.2), (1002, 66020.3, 66035.3)]


def _as_terminal_array(rows) -> np.ndarray:
    arr = np.zeros(len(rows), dtype=_MT5_TICK_DTYPE)
    for i, (msc, bid, ask) in enumerate(rows):
        arr[i]["time"] = msc // 1000
        arr[i]["time_msc"] = msc
        arr[i]["bid"] = bid
        arr[i]["ask"] = ask
    return arr


class _FakeMt5:
    """端末の代役（読み取りのみ・MetaTrader5 に依存しない）。"""

    COPY_TICKS_INFO = 1

    def __init__(self, rows):
        self._rows = rows

    def symbol_select(self, symbol, enable=True):
        return True

    def copy_ticks_from(self, symbol, frm, count, flags):
        return None if self._rows is None else _as_terminal_array(self._rows)[:count]

    def copy_ticks_range(self, symbol, frm, to, flags):
        return None if self._rows is None else _as_terminal_array(self._rows)

    def last_error(self):
        return (-10005, "no ipc connection")

    def account_info(self):
        return SimpleNamespace(server=_SERVER_NAME)


@contextmanager
def _serving(rows):
    """VM 側 feed を一時ポートで立て、実 HTTP の供給元を渡す。"""
    server = HTTPServer(
        ("127.0.0.1", 0),
        feed.make_handler(
            mt5=_FakeMt5(rows), secret=_SECRET, key_id=_KEY_ID, nonces=feed.NonceCache()
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, portno = server.server_address[:2]
    try:
        yield http_source.HttpTickSource(
            f"http://{host}:{portno}", key_id=_KEY_ID, secret=_SECRET
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _http_implementation(rows):
    with _serving(rows) as source:
        yield source


@contextmanager
def _fake_implementation(rows):
    yield fakes.FakeTickSource(rows)


@contextmanager
def _spy_implementation(rows):
    yield fakes.CountingTickSource(rows)


@contextmanager
def _failing_http():
    """端末が応答しない実 HTTP（502 → 供給例外）。"""
    with _serving(None) as source:
        yield source


@contextmanager
def _failing_fake():
    yield fakes.FailingTickSource(SupplyUnavailable("端末が一時的に応答しません"))


class _FailingSpy(fakes.CountingTickSource):
    """数えつつ必ず失敗する Spy（数えた要求が例外で終わることを示す）。"""

    def fetch(self, **kwargs):
        super().fetch(**kwargs)
        raise SupplyUnavailable("端末が一時的に応答しません")


@contextmanager
def _failing_spy():
    yield _FailingSpy(_TAPE)


#: 契約を課す 3 実装（framework の実 HTTP / 検定用 Fake / 数える Spy）。
_IMPLEMENTATIONS = {
    "http": (_http_implementation, _failing_http),
    "fake": (_fake_implementation, _failing_fake),
    "spy": (_spy_implementation, _failing_spy),
}


@pytest.fixture(params=sorted(_IMPLEMENTATIONS), ids=sorted(_IMPLEMENTATIONS))
def implementation(request):
    """3 実装を 1 本の検定に通す（新しい実装を足したら本表に足す）。"""
    return _IMPLEMENTATIONS[request.param]


def _fetched(source, *, symbol="JP225", from_msc=1000, to_msc=None, max_rows=10):
    """契約どおり（キーワード専用）に 1 回取得する。

    ``None`` が返ったらそこで止める。失敗を戻り値で表す実装を「0 行だった」と読み替えると、
    取りこぼしが正常な運転として台帳へ入る。
    """
    response = source.fetch(
        symbol=symbol, from_msc=from_msc, to_msc=to_msc, max_rows=max_rows
    )
    assert response is not None, "供給の失敗を None で表してはならない（例外で表す）"
    return response


# =====================================================================
# 契約 1: キーワード専用の 4 引数
# =====================================================================

def test_fetch_takes_exactly_the_four_keyword_only_arguments(implementation):
    """引数は名前で渡す。位置で受ける実装は順序の入れ替わりを検出できない。"""
    open_source, _ = implementation

    with open_source(_TAPE) as source:
        kinds = {
            name: parameter.kind
            for name, parameter in inspect.signature(source.fetch).parameters.items()
        }

    assert kinds == {
        name: inspect.Parameter.KEYWORD_ONLY
        for name in ("symbol", "from_msc", "to_msc", "max_rows")
    }


# =====================================================================
# 契約 2: 0 行応答は成功であり、窓の下端を後退させない
# =====================================================================

def test_an_empty_result_keeps_the_window_floor_where_it_was(implementation):
    """0 行なら ``latest_msc == from_msc``。

    0 行を「進んだ」と読むと次の窓が前へ飛び、その間のティックを永久に取りこぼす。
    """
    open_source, _ = implementation

    with open_source([]) as source:
        response = _fetched(source, from_msc=1234)

    assert (response.rows, response.count) == ([], 0)
    assert response.latest_msc == 1234


# =====================================================================
# 契約 3: 失敗は例外（戻り値で表さない）
# =====================================================================

def test_a_failure_is_raised_and_never_returned(implementation):
    """供給できないときに 0 行の応答を返さない（黙って欠測を作らない）。"""
    _, open_failing = implementation

    with open_failing() as source:
        with pytest.raises(Mt5SupplyError):
            source.fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)


# =====================================================================
# 契約 4: truncated は「max_rows を超えた」ことと一致する
# =====================================================================

@pytest.mark.parametrize("max_rows,expected", [(2, True), (3, False), (10, False)])
def test_truncated_says_whether_more_rows_were_available(implementation, max_rows, expected):
    """``truncated`` が真になるのは、返せる行が ``max_rows`` を超えたときだけである。

    切り詰めの有無は呼び出し側が「同じ窓をもう一度取るか」を決める唯一の手掛かりである。
    """
    open_source, _ = implementation

    with open_source(_TAPE) as source:
        response = _fetched(source, max_rows=max_rows)

    assert response.truncated is expected
    assert len(response.rows) == min(max_rows, len(_TAPE))


# =====================================================================
# 契約検定そのものの効き目（isinstance が通す実装を落とす）
# =====================================================================

class _NameOnlySource:
    """名前だけ合わせた実装（引数が違い、失敗を ``None`` で表す）。"""

    def fetch(self, cursor_ms=0):
        return None


def test_isinstance_accepts_an_implementation_that_the_contract_rejects():
    """``isinstance`` は名前しか見ない（実測）。契約は振る舞いで落とす。

    この 1 本が無いと、上の 4 本が「たまたま正しい 3 実装で緑」なだけなのか、
    誤った実装を落とせるのかが分からない。
    """
    lookalike = _NameOnlySource()

    assert isinstance(lookalike, port.IncrementalTickSource)
    with pytest.raises(TypeError):
        lookalike.fetch(symbol="JP225", from_msc=1000, to_msc=None, max_rows=10)


def test_the_contract_helper_refuses_a_source_that_returns_none():
    """失敗を ``None`` で表す実装は契約検定の入口で止まる。"""

    class _SilentNone:
        def fetch(self, *, symbol, from_msc, to_msc, max_rows):
            return None

    with pytest.raises(AssertionError):
        _fetched(_SilentNone())
