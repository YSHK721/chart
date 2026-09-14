"""live core 静的配信の最小権限（ISSUE-473）。

live core の ``_resolve_static`` は web/ ルート全体を許可根としていたため、
/tests/*.test.js・/package.json・/ISSUE.md・/prototype_* といった URL まで HTTP 200 で
露出していた（server.py の「tests 等は露出しない」記述と不一致）。統合ルータ
（unified_ui/router.py の _ASSET_FILES / _ASSET_SUBTREE_PREFIXES・ISSUE-278 #9）と
replay 側 StaticFileServer（資産サブツリーのみ許可）が既に採る許可規則を live core にも
適用し、配信面をエントリ（index.html）＋資産サブツリー（js/css/vendor/data）に限定する。

固定する不変条件:
  R1: 開発用ファイル（tests/・package.json・ISSUE.md・prototype_*）は 404。
  R2: 実配信面（index.html・js/・css/・vendor/・data/）は従来どおり配信される。
      js/ の共有 symlink（供給パッケージ <pkg>/web/js への解決）も従来どおり許可される。
  R3: パストラバーサル（許可サブツリー経由の ``..`` 逸脱を含む）は 404。

様式は test_server_get_routing_table.py（HTTP 実行時ガード＋ユニット）を踏襲する。
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import framework.server as server_mod
from framework.server import IndicatorUIRequestHandler


# --------------------------------------------------------------------------- #
# ユニット: _resolve_static の許可集合
# --------------------------------------------------------------------------- #

#: R1: 配信してはならないパス（いずれも web/ ルート配下に実在するファイル）。
_DENIED = [
    "/tests/catalog.test.js",
    "/package.json",
    "/ISSUE.md",
    "/prototype_260630-01/out/sr_recent.png",
]

#: R2: 配信し続けるパス（live front が実際に要求する資産面）。
_ALLOWED = [
    "/",                              # index.html
    "/index.html",
    "/css/app.css",
    "/vendor/lightweight-charts.js",
    "/data/trade_markers.json",       # index.html の tradeMarkers.load が要求する
    "/js/usecase/form_model.js",      # 共有 symlink（供給パッケージへ resolve される）
]


@pytest.mark.parametrize("path", _DENIED)
def test_development_files_are_not_resolved(path):
    assert server_mod._resolve_static(path) is None, (
        f"{path} が配信面に露出している（最小権限規則の未適用・ISSUE-473）"
    )


@pytest.mark.parametrize("path", _ALLOWED)
def test_asset_files_are_still_resolved(path):
    target = server_mod._resolve_static(path)
    assert target is not None and target.is_file(), (
        f"{path} が解決されない（許可規則が実配信面を狭めすぎている）"
    )


def test_traversal_through_an_allowed_subtree_is_rejected():
    """R3: 許可サブツリーを経由した ``..`` 逸脱（/css/../tests/…）も拒否される。"""
    assert server_mod._resolve_static("/css/../tests/catalog.test.js") is None
    assert server_mod._resolve_static("/../../ISSUE.md") is None


# --------------------------------------------------------------------------- #
# 実行時ガード: HTTP で 404 / 200 を固定
# --------------------------------------------------------------------------- #
@pytest.fixture()
def server():
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


def _get_status(base: str, path: str) -> int:
    try:
        with urllib.request.urlopen(base + path, timeout=60) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_tests_js_returns_404_over_http(server):
    """ISSUE-473 の実測再現の反転: /tests/*.test.js は HTTP 404 になる。"""
    assert _get_status(server, "/tests/catalog.test.js") == 404


def test_asset_face_returns_200_over_http(server):
    for path in ("/index.html", "/css/app.css", "/data/trade_markers.json"):
        assert _get_status(server, path) == 200, f"{path} は配信し続ける"
