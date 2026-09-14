"""AllowlistFileRoutes（framework 層・Phase 5 R-1）の単体検定。

固定する不変条件:
    1. 許可集合に**ちょうど**一致する path だけが内側の配信器へ委譲される。
    2. それ以外はすべて 404 で、**内側の配信器は 1 度も呼ばれない**（到達不能の構造担保）。
       report_ui の vendor 根には v4 の lightweight-charts.standalone.js が同居しており、
       「配信器へ渡してから内側が弾く」形にすると、内側の許可根判定を 1 つ変えただけで
       v4 が露出する。渡さないことで NFR-07 を構造で担保する。
    3. 許可集合（`allowed`）は合成根が持つ（本クラスはリテラルを 1 つも持たない）。
    4. パストラバーサル（`..` 混じり）・prefix 共有・末尾スラッシュは一致しない＝404。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.framework.allowlist_file_routes import AllowlistFileRoutes


class _FakeHandler:
    """`send_response` / `end_headers` の呼び出しだけを記録する BaseHTTPRequestHandler ダブル。"""

    def __init__(self) -> None:
        self.responses: "list[int]" = []
        self.headers_ended = 0

    def send_response(self, code: int) -> None:
        self.responses.append(code)

    def end_headers(self) -> None:
        self.headers_ended += 1


class _FakeInner:
    """委譲先ダブル（受け取った path を記録し、内側が呼ばれたことを実証する）。"""

    def __init__(self) -> None:
        self.calls: "list[str]" = []

    def serve(self, handler, path: str) -> str:
        self.calls.append(path)
        return "served"


@pytest.fixture
def routes():
    inner = _FakeInner()
    return inner, AllowlistFileRoutes(inner, allowed={"/chart.umd.js"})


# --- 1. 許可されたファイルだけが内側へ届く ----------------------------------

def test_許可ファイルは内側へそのまま委譲される(routes) -> None:
    inner, gate = routes
    handler = _FakeHandler()
    result = gate.serve(handler, "/chart.umd.js")
    assert inner.calls == ["/chart.umd.js"]
    assert result == "served"
    assert handler.responses == []


# --- 2. 非許可は 404 かつ内側へ到達しない -----------------------------------

@pytest.mark.parametrize("path", [
    "/lightweight-charts.standalone.js",  # v4 バンドル（同じ根に同居している）
    "/",                                   # 根の index（ディレクトリ列挙を作らない）
    "",
    "/chart.umd.js/",                      # 末尾スラッシュ
    "/chart.umd.js.map",                   # 接頭辞を共有する別資産
    "/CHART.UMD.JS",                       # 大文字小文字は一致とみなさない
    "/../vendor/lightweight-charts.standalone.js",
    "/subdir/chart.umd.js",
])
def test_非許可パスは404で内側へ渡らない(routes, path: str) -> None:
    inner, gate = routes
    handler = _FakeHandler()
    gate.serve(handler, path)
    assert inner.calls == []
    assert handler.responses == [404]
    assert handler.headers_ended == 1


# --- 3. 許可集合は外から与える（クラスはリテラルを持たない）-----------------

def test_許可集合は与えたものがそのまま面に出る() -> None:
    inner = _FakeInner()
    gate = AllowlistFileRoutes(inner, allowed=["/a.js", "/b.js"])
    assert gate.allowed == frozenset({"/a.js", "/b.js"})


def test_許可集合が空なら何も配信しない() -> None:
    inner = _FakeInner()
    gate = AllowlistFileRoutes(inner, allowed=())
    handler = _FakeHandler()
    gate.serve(handler, "/chart.umd.js")
    assert inner.calls == []
    assert handler.responses == [404]


def test_複数許可のうち一致したものだけが通る() -> None:
    inner = _FakeInner()
    gate = AllowlistFileRoutes(inner, allowed={"/a.js", "/b.js"})
    gate.serve(_FakeHandler(), "/b.js")
    gate.serve(_FakeHandler(), "/c.js")
    assert inner.calls == ["/b.js"]
