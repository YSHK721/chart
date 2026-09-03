"""GetRouteResponder への書き出し器の加法注入（ISSUE-479 Wave2 3-3）。

なぜ必要か:
    replay 側の JSON 応答ヘッダは sim 側と**異なる**（``Content-Type: application/json``
    に charset が付かない）。この違いは front と front の検定が見ている実体なので、
    分割のついでに統一してはならない（応答 byte が変わる）。一方で「prefix でルートを選び、
    外れたら静的配信へ落とす」という骨格は完全に同じで、2 つ書く理由が無い。

    したがって**書き出し方だけ**を注入で受け取れるようにする。既定は現行の write_json で、
    渡さなければ挙動は 1 ビットも変わらない（加法）。

もう 1 点（クエリ透過）:
    sim 側の呼び出しは呼ぶ前にクエリを落とした path を渡すが、replay のルートはクエリを
    読む（``/candles?datasetRef=...``）。prefix の一致判定と fallback へ渡す値は
    **クエリを落とした path**、ルート関数へ渡す値は **元の path** とする。
    クエリを含まない呼び出し（sim 側の現行 3 経路）では両者が同一なので挙動不変である。
"""

from __future__ import annotations

import pytest

from simulator.sim_ui.adapter.job_api_controller import ApiResponse
from simulator.sim_ui.framework.json_get_routes import GetRouteResponder, write_json


class _FakeHandler:
    """送信された応答を丸ごと記録する Handler の身代わり。"""

    def __init__(self) -> None:
        self.status: "int | None" = None
        self.headers: "list[tuple[str, str]]" = []
        self.ended = False
        self.written = b""
        self.wfile = self

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, key: str, value: str) -> None:
        self.headers.append((key, value))

    def end_headers(self) -> None:
        self.ended = True

    def write(self, data: bytes) -> None:
        self.written += data


class _FakeFallback:
    def __init__(self) -> None:
        self.served: "list[str]" = []

    def serve(self, handler, path: str) -> None:
        self.served.append(path)


def _response() -> ApiResponse:
    return ApiResponse(status=200, payload={"ok": True})


# --------------------------------------------------------------------------------------
# 1. 既定は現行の書き出し（加法＝渡さなければ何も変わらない）
# --------------------------------------------------------------------------------------
def test_the_default_writer_is_the_current_one() -> None:
    responder = GetRouteResponder(routes={"/x": lambda _p: _response()}, fallback=_FakeFallback())
    handler = _FakeHandler()
    responder.serve(handler, "/x")
    assert handler.status == 200
    assert ("Content-Type", "application/json; charset=utf-8") in handler.headers
    assert ("Cache-Control", "no-store") in handler.headers


def test_the_default_writer_matches_write_json_byte_for_byte() -> None:
    """既定経路が write_json そのものであること（第 2 実装を作っていない）。"""
    through_responder = _FakeHandler()
    GetRouteResponder(
        routes={"/x": lambda _p: _response()}, fallback=_FakeFallback()
    ).serve(through_responder, "/x")
    direct = _FakeHandler()
    write_json(direct, _response())
    assert (through_responder.status, through_responder.headers, through_responder.written) == (
        direct.status, direct.headers, direct.written
    )


# --------------------------------------------------------------------------------------
# 2. 書き出し器の注入
# --------------------------------------------------------------------------------------
def test_an_injected_writer_is_used_instead_of_the_default() -> None:
    """replay のヘッダ（charset なし）を sim と統一せずに骨格だけ共有するための注入。"""
    calls: "list[tuple[int, dict]]" = []

    def _writer(handler, response) -> None:
        calls.append((response.status, dict(response.payload)))

    handler = _FakeHandler()
    GetRouteResponder(
        routes={"/x": lambda _p: _response()}, fallback=_FakeFallback(), writer=_writer
    ).serve(handler, "/x")
    assert calls == [(200, {"ok": True})]
    assert handler.status is None, "既定の書き出しが併走しています（二重送信）"


def test_the_injected_writer_is_not_used_for_the_static_fallback() -> None:
    """ルートに当たらない path は静的配信へ落ちる（書き出し器は関与しない）。"""
    calls: "list[int]" = []
    fallback = _FakeFallback()
    GetRouteResponder(
        routes={"/x": lambda _p: _response()},
        fallback=fallback,
        writer=lambda _h, _r: calls.append(1),
    ).serve(_FakeHandler(), "/y.js")
    assert fallback.served == ["/y.js"]
    assert calls == []


# --------------------------------------------------------------------------------------
# 3. クエリ透過（一致判定と fallback は path・ルート関数へは元の値）
# --------------------------------------------------------------------------------------
def test_a_query_string_does_not_break_the_prefix_match() -> None:
    seen: "list[str]" = []

    def _route(path: str) -> ApiResponse:
        seen.append(path)
        return _response()

    responder = GetRouteResponder(routes={"/candles": _route}, fallback=_FakeFallback())
    responder.serve(_FakeHandler(), "/candles?datasetRef=jp225_m1&limit=2")
    assert seen == ["/candles?datasetRef=jp225_m1&limit=2"], "ルートは元の path を受け取る"


def test_the_fallback_receives_the_path_without_the_query() -> None:
    """静的配信にクエリを渡さない（現行の呼び出しと同じ値になる）。"""
    fallback = _FakeFallback()
    GetRouteResponder(routes={"/x": lambda _p: _response()}, fallback=fallback).serve(
        _FakeHandler(), "/asset.js?v=3"
    )
    assert fallback.served == ["/asset.js"]


@pytest.mark.parametrize(
    "path", ["/x", "/x/1", "/x?a=1", "/x/1?a=1"], ids=["exact", "child", "query", "child_query"]
)
def test_the_route_is_chosen_on_the_boundary_regardless_of_the_query(path: str) -> None:
    responder = GetRouteResponder(routes={"/x": lambda _p: _response()}, fallback=_FakeFallback())
    handler = _FakeHandler()
    responder.serve(handler, path)
    assert handler.status == 200, path


@pytest.mark.parametrize("path", ["/x-extra.js", "/xy", "/x-extra.js?v=1"])
def test_a_neighbouring_asset_is_not_swallowed_by_the_prefix(path: str) -> None:
    """区切り境界で判定する（``startswith`` だけだと別資産を吸い込む）。"""
    fallback = _FakeFallback()
    GetRouteResponder(routes={"/x": lambda _p: _response()}, fallback=fallback).serve(
        _FakeHandler(), path
    )
    assert fallback.served == [path.split("?")[0]], path


# --------------------------------------------------------------------------------------
# 4. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("routes_declared", [1, 8], ids=["routes_1", "routes_8"])
def test_one_request_resolves_exactly_one_route(routes_declared: int) -> None:
    """ルート数 1 / 8 の 2 点で「ルート関数の発行 − 応答に使った数 = 0」。

    ルートを増やしても 1 リクエストで走るルート関数は 1 つだけである（前方の
    ルートを試しに実行して捨てる、という形になっていない）。
    """
    # Arrange
    ran: "list[str]" = []

    def _make(name: str):
        def _route(_path: str) -> ApiResponse:
            ran.append(name)
            return _response()

        return _route

    routes = {f"/r{i}": _make(f"r{i}") for i in range(routes_declared)}
    responder = GetRouteResponder(routes=routes, fallback=_FakeFallback())
    # Act
    responder.serve(_FakeHandler(), "/r0?q=1")
    # Assert（応答に使ったルートは 1 つ）
    responses_produced = 1
    assert len(ran) - responses_produced == 0, ran


def test_a_missing_route_runs_no_route_function_at_all() -> None:
    """外れたリクエストでルート関数を 1 つも走らせない（捨てる計算を作らない）。"""
    ran: "list[int]" = []
    GetRouteResponder(
        routes={f"/r{i}": (lambda _p: ran.append(i) or _response()) for i in range(8)},
        fallback=_FakeFallback(),
    ).serve(_FakeHandler(), "/nowhere.js")
    assert len(ran) - 0 == 0
