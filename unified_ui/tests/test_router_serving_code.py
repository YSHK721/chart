"""配信中のコードの申告口の契約テスト（ISSUE-531）。

なぜ口が要るか: `/__serving_root` は「どのツリーか」しか答えない。同じツリーでコードを
更新しても Python は起動時のまま動くため、起動側が「そのツリーの**どの内容**で起動したか」を
照合できなければ、古いコードを黙って見続ける（2026-10-01 に実 UI 確認が 3 回止まった）。

ルータは同一性の規則を 1 行も持たない。起動側（serve.sh）が core を起動する**前**に作った
申告を受け取り、そのまま配るだけである。本ファイルが固定するのは**配管**:
口が在り、上流へ透過せず、受け取った申告をそのまま返し、キャッシュさせない。
申告を受け取っていなければ 404 を返す（「分からない」を「同じ」に見せない）。

様式は `/__serving_root` に揃える（平文・1 行目だけで判断できる）。

構造は AAA。テスト名は「対象_条件_期待結果」。
"""

from __future__ import annotations

import threading

import pytest

import router as router_mod
from test_router import WEB_ROOT, _base_url, _request, _make_stub_upstream

_LINE = "0123abcd 1759300000.000"


def _start(serving_code):
    live_srv, _ = _make_stub_upstream("live")
    kwargs = {} if serving_code is None else {"serving_code": serving_code}
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={"live": _base_url(live_srv)},
        web_root=WEB_ROOT,
        **kwargs,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, live_srv, live_srv.records


@pytest.fixture()
def router_with_code():
    server, live_srv, seen = _start(_LINE)
    yield server, seen
    server.shutdown()
    live_srv.shutdown()


@pytest.fixture()
def router_without_code():
    server, live_srv, seen = _start(None)
    yield server, seen
    server.shutdown()
    live_srv.shutdown()


def test_serving_code_受け取った申告をそのまま平文で返す(router_with_code):
    # Arrange
    server, _ = router_with_code
    # Act
    resp = _request(server, "GET", "/__serving_code")
    # Assert
    assert resp.status == 200
    assert resp.body.decode("utf-8") == _LINE + "\n"
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_serving_code_上流へ透過しない(router_with_code):
    # Arrange
    server, seen = router_with_code
    # Act
    _request(server, "GET", "/__serving_code?x=1")
    # Assert
    assert seen == []


def test_serving_code_申告が無ければ404で分からないと答える(router_without_code):
    # Arrange
    server, _ = router_without_code
    # Act
    resp = _request(server, "GET", "/__serving_code")
    # Assert
    assert resp.status == 404


def test_main_serving_code引数をサーバへ渡す(monkeypatch):
    """CLI の `--serving-code` が `create_router_server` へ届くこと（受け口だけ作って結線しない形を防ぐ）。"""
    # Arrange
    captured = {}

    class _Stop(Exception):
        pass

    def fake_create(bind_addr, **kwargs):
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setattr(router_mod, "create_router_server", fake_create)
    # Act
    with pytest.raises(_Stop):
        router_mod.main(["0", "--serving-code", _LINE])
    # Assert
    assert captured.get("serving_code") == _LINE
