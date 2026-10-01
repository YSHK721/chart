"""供給の健全性をプロセス外から観測する口の契約テスト（ISSUE-526 段 3）。

なぜ口が要るか: 供給が止まっているかどうかは、供給を書いている常駐の**内側**でしか分から
なかった。ISSUE-524 では受け側の常駐が起動時 Fail-Stop でログ 1 行だけ残して消え、8 日間
誰も気づかなかった。判定の材料をプロセス外から観測可能にしないと、照合は原理的に不可能になる
（`/__serving_root` を足した ISSUE-348 と同じ構図）。

様式は `/__serving_root` に揃える: 平文・1 行目だけで判断できる・キャッシュさせない
（起動スクリプトへ jq 等の依存を持ち込まない）。

ルータ自身は判定規則を 1 行も持たない。報告は marketdata 側の単一の定義が作り、ルータは
それを配るだけである。したがって本ファイルが固定するのは**配管**（口が在り、上流へ透過せず、
差し替えた報告をそのまま返し、1 回の要求で 1 回だけ観測する）であり、報告の中身の規則は
marketdata/tests/test_supply_health.py が固定する。

構造は AAA。テスト名は「対象_条件_期待結果」。
"""

from __future__ import annotations

import threading

import pytest

import router as router_mod
from test_router import WEB_ROOT, _base_url, _request, _make_stub_upstream

#: 差し替え用の報告（1 行目が総合判定・2 行目以降が供給ごと）。
_ABNORMAL_REPORT = "stopped\ndukascopy healthy\nmt5 stopped\n"


class _FakeProbe:
    """固定の報告を返す観測器（呼ばれた回数を数える）。

    ルータが「報告をそのまま配る」以外のことをしていないかを見るための代役である。手書きの
    偽物にしているのは、`report` 以外の呼び出しが在れば AttributeError で落ちてほしいため。
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    def report(self) -> str:
        self.calls += 1
        return self._text


@pytest.fixture()
def probe():
    return _FakeProbe(_ABNORMAL_REPORT)


@pytest.fixture()
def router_with_probe(probe):
    """報告を差し替えたルータ（上流 2 本つき）。"""
    live_srv, _ = _make_stub_upstream("live")
    replay_srv, _ = _make_stub_upstream("replay")
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={"live": _base_url(live_srv), "replay": _base_url(replay_srv)},
        web_root=WEB_ROOT,
        supply_health=probe,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, live_srv, replay_srv
    server.shutdown()
    live_srv.shutdown()
    replay_srv.shutdown()


@pytest.fixture()
def router_with_default_probe():
    """報告を差し替えないルータ（出荷時の結線をそのまま使う）。"""
    live_srv, _ = _make_stub_upstream("live")
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={"live": _base_url(live_srv)},
        web_root=WEB_ROOT,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    live_srv.shutdown()


def test_supply_health_answers_the_report_in_plain_text(router_with_probe):
    # Arrange
    server, _live, _replay = router_with_probe
    # Act
    resp = _request(server, "GET", router_mod._SUPPLY_HEALTH_PATH)
    # Assert: 平文でそのまま配る（ルータが体裁を作り直さない）
    assert resp.status == 200, f"expected 200, got {resp.status}/{resp.error}"
    assert resp.body.decode("utf-8") == _ABNORMAL_REPORT
    assert resp.headers.get("Content-Type", "").startswith("text/plain")


def test_supply_health_puts_the_abnormal_overall_verdict_on_the_first_line(
    router_with_probe,
):
    # Arrange: 異常を健全へ塗り潰す実装だと、ここが "healthy" になる
    server, _live, _replay = router_with_probe
    # Act
    resp = _request(server, "GET", router_mod._SUPPLY_HEALTH_PATH)
    # Assert
    assert resp.body.decode("utf-8").splitlines()[0] == "stopped"


def test_supply_health_is_not_proxied_to_any_upstream(router_with_probe):
    # Arrange: core へ透過させると core の 404 が返り「答えない」状態になる
    server, live_srv, replay_srv = router_with_probe
    # Act
    _request(server, "GET", router_mod._SUPPLY_HEALTH_PATH)
    # Assert
    assert (len(live_srv.records), len(replay_srv.records)) == (0, 0)


def test_supply_health_answers_with_query_string(router_with_probe):
    # Arrange: 呼び出し側がキャッシュ回避のクエリを付けても答える必要がある
    server, _live, _replay = router_with_probe
    # Act
    resp = _request(server, "GET", f"{router_mod._SUPPLY_HEALTH_PATH}?t=1")
    # Assert
    assert (resp.status, resp.body.decode("utf-8")) == (200, _ABNORMAL_REPORT)


def test_supply_health_is_not_cached(router_with_probe):
    # Arrange: 供給の状態は変わるので、古い答えを掴ませてはならない
    server, _live, _replay = router_with_probe
    # Act
    resp = _request(server, "GET", router_mod._SUPPLY_HEALTH_PATH)
    # Assert
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_one_request_observes_exactly_once(router_with_probe, probe):
    """要求 1 回につき観測 1 回（発行した観測 − 求められた報告 = 0）。

    計算量テスト（絶対命令）。行ごとに観測し直す実装でも応答の中身は同じ値になるため、
    状態検証では原理的に落ちない。
    """
    # Arrange
    server, _live, _replay = router_with_probe
    # Act
    _request(server, "GET", router_mod._SUPPLY_HEALTH_PATH)
    after_one = probe.calls
    _request(server, "GET", router_mod._SUPPLY_HEALTH_PATH)
    _request(server, "GET", router_mod._SUPPLY_HEALTH_PATH)
    _request(server, "GET", router_mod._SUPPLY_HEALTH_PATH)
    after_four = probe.calls
    # Assert: 叩いた回数だけで決まる（回数そのものを焼き込まない）
    assert after_one > 0, "Spy が何も数えていない（計算量の検定が恒真に退化している）"
    assert (after_one - 1, after_four - after_one - 3) == (0, 0)


def test_supply_health_cannot_be_stolen_by_a_mode_prefix():
    """口の名前はモード名として使えない（モードに奪われると口が消える）。"""
    # Arrange
    name = router_mod._SUPPLY_HEALTH_PATH.lstrip("/")
    # Act / Assert
    assert name in router_mod.RESERVED_MODE_NAMES
    with pytest.raises(ValueError):
        router_mod.parse_upstream_args([f"{name}=http://127.0.0.1:9999"])


def test_the_default_wiring_answers_the_shipped_supplies(router_with_default_probe):
    """差し替えなしでも答える（受け口を作っても結線しなければ無言で死ぬ・ISSUE-291）。

    値は供給の実状態で変わるため焼き込まない。固定するのは「出荷台帳の供給が全部並び、
    どの判定も語彙の中に在る」ことである。
    """
    # Arrange
    from marketdata import supply_health

    vocabulary = {
        supply_health.HEALTHY,
        supply_health.STUCK,
        supply_health.STOPPED,
        supply_health.UNDECIDABLE,
    }
    # Act
    resp = _request(router_with_default_probe, "GET", router_mod._SUPPLY_HEALTH_PATH)
    lines = resp.body.decode("utf-8").splitlines()
    rows = dict(line.split() for line in lines[1:])
    # Assert
    assert resp.status == 200, f"expected 200, got {resp.status}/{resp.error}"
    assert sorted(rows) == sorted(supply_health.SUPPLIES)
    assert set(rows.values()) | {lines[0]} <= vocabulary
