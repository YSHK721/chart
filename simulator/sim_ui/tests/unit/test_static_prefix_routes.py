"""static_prefix_routes（prefix → 静的配信の表・framework 層・Phase 4 F-5）の単体検定。

固定する不変条件:
    1. 登録 prefix 配下の GET は、**prefix を除いたパス**で当該配信器へ委譲される
       （配信器は自分の根しか知らない＝根の解決を二重定義しない）。
    2. prefix ちょうど（``/report-js``）も当該配信器へ委譲される（境界・"/"）。
    3. prefix と**接頭辞だけ**を共有する別パス（``/report-jsx.js``）は委譲しない。
       `str.startswith` だけで判定すると、別資産まで別根へ吸い込む（`json_get_routes`
       と同じ壊れ方）。
    4. 未登録パスは fallback（既存の `StaticFileServer`）へ**素通し**される
       ＝既存配信面は 1 バイトも変わらない。
    5. 面は `StaticFileServer` と同一の ``serve(handler, path)``（LSP）。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.framework.static_prefix_routes import StaticPrefixRoutes


class _RecordingServer:
    """``serve(handler, path)`` を持つ配信器のダブル（受けたパスだけ記録する）。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: "list[tuple[object, str]]" = []

    def serve(self, handler, path: str) -> str:
        self.calls.append((handler, path))
        return self.name


@pytest.fixture
def routes():
    js = _RecordingServer("js")
    css = _RecordingServer("css")
    fallback = _RecordingServer("fallback")
    table = StaticPrefixRoutes(
        routes={"/report-js": js, "/report-css": css}, fallback=fallback
    )
    return table, js, css, fallback


# --- 1. prefix 除去後のパスで委譲（不変条件 1）-------------------------------

def test_prefix配下はprefixを除いたパスで委譲される(routes) -> None:
    table, js, _css, _fb = routes
    handler = object()
    table.serve(handler, "/report-js/chart.js")
    assert js.calls == [(handler, "/chart.js")]


def test_入れ子のパスもprefixだけが外れる(routes) -> None:
    table, js, _css, _fb = routes
    table.serve(object(), "/report-js/tests/chart.test.mjs")
    assert js.calls[0][1] == "/tests/chart.test.mjs"


def test_別のprefixは別の配信器へ行く(routes) -> None:
    table, js, css, _fb = routes
    table.serve(object(), "/report-css/style.css")
    assert css.calls[0][1] == "/style.css"
    assert js.calls == []


# --- 2. 境界（不変条件 2）----------------------------------------------------

def test_prefixちょうどは当該配信器へ根のパスで委譲される(routes) -> None:
    table, js, _css, _fb = routes
    table.serve(object(), "/report-js")
    assert js.calls[0][1] == "/"


# --- 3. 接頭辞の共有では委譲しない（不変条件 3）------------------------------

@pytest.mark.parametrize("path", ["/report-jsx.js", "/report-js.js", "/report-cssx/y"])
def test_接頭辞を共有する別パスはfallbackへ落ちる(routes, path: str) -> None:
    table, js, css, fb = routes
    table.serve(object(), path)
    assert js.calls == []
    assert css.calls == []
    assert fb.calls[0][1] == path


# --- 4. 未登録は fallback へ素通し（不変条件 4）------------------------------

@pytest.mark.parametrize("path", ["/", "/index.html", "/js/boot.js", "/vendor/x.js"])
def test_未登録パスはfallbackへ素通しされる(routes, path: str) -> None:
    table, _js, _css, fb = routes
    handler = object()
    table.serve(handler, path)
    assert fb.calls == [(handler, path)]


def test_表が空なら常にfallbackへ行く() -> None:
    fb = _RecordingServer("fallback")
    table = StaticPrefixRoutes(routes={}, fallback=fb)
    table.serve(object(), "/report-js/chart.js")
    assert fb.calls[0][1] == "/report-js/chart.js"


# --- 5. 面の同一性（不変条件 5）----------------------------------------------

def test_戻り値は委譲先の戻り値をそのまま返す(routes) -> None:
    """`StaticFileServer.serve` は None を返すが、面としては素通しであることを固定する。"""
    table, _js, _css, _fb = routes
    assert table.serve(object(), "/report-js/chart.js") == "js"
    assert table.serve(object(), "/nope.js") == "fallback"
