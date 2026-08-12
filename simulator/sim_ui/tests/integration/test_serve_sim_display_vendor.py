"""表示層 vendor 配信（Phase 5 R-1）の結合検定（実 HTTP）。

Phase 5 の比較・判定タブ（FR-17/18）は移植元 compare.js が描くが、compare.js は
Chart.js（UMD global `Chart`）が無いと**無音でスキップ**する（`typeof Chart === "undefined"`
の早期 return）。したがって sim の子文書へ Chart.js を供給する経路が要る。

固定する不変条件:
    1. `GET /report-vendor/chart.umd.js` が report_ui の vendor 実体そのものを 200 で返す
       （Chart.js v4.4.1・無改変・sim 側に写しを置かない）。
    2. **同じ vendor 根に同居する lightweight-charts v4.1.3 へはどの経路でも到達できない**
       （NFR-07）。allowlist に載っていないので内側の配信器まで届かない。
    3. allowlist は合成根が持ち、`chart.umd.js` **1 ファイルだけ**である（構造での担保）。
    4. 既存の配信面（/report-js, /report-css, 静的面, JSON 面）は 1 バイトも変わらない。

方式: 実 HTTP（port=0 の空きポート）。既存 `test_serve_sim_display.py` と同方式。
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from simulator.sim_ui.framework.serve_sim_display import make_server
from simulator.sim_ui.main.composition_root_display import (
    REPORT_VENDOR_ALLOWED,
    REPORT_VENDOR_PREFIX,
    build_sim_display_app,
)

_ROOT = Path(__file__).resolve().parents[4]
_REPORT_WEB = _ROOT / "simulator" / "report_ui" / "web"
_SIM_WEB = _ROOT / "simulator" / "sim_ui" / "web"

#: 同じ vendor 根に同居する v4 lightweight-charts（統合ページの v5.2.0 と衝突する実体）。
_V4_LWC = "lightweight-charts.standalone.js"


def _serve(app):
    srv = make_server(app, "127.0.0.1", None)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{port}"


def _request(base, path):
    req = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


@pytest.fixture
def display(tmp_path: Path):
    app = build_sim_display_app(
        repo_root=_ROOT, web_dir=_SIM_WEB, data_root=tmp_path / "data"
    )
    srv, thread, base = _serve(app)
    try:
        yield base, app
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


# --- 1. Chart.js の実体が返る（不変条件 1）----------------------------------

def test_chart_umd_jsが実体そのままで返る(display) -> None:
    base, _app = display
    status, body, headers = _request(base, f"{REPORT_VENDOR_PREFIX}/chart.umd.js")
    assert status == 200
    assert body == (_REPORT_WEB / "vendor" / "chart.umd.js").read_bytes()
    assert headers["Content-Type"] == "application/javascript; charset=utf-8"


def test_返るのはChartjs_v4_4_1である(display) -> None:
    """バージョンの無断変更を検出する（R-1 で承認された版はこれ 1 つ）。"""
    base, _app = display
    _status, body, _h = _request(base, f"{REPORT_VENDOR_PREFIX}/chart.umd.js")
    assert b"Chart.js v4.4.1" in body[:400]


# --- 2. v4 lightweight-charts へは到達できない（不変条件 2・NFR-07）---------

@pytest.mark.parametrize("path", [
    f"/report-vendor/{_V4_LWC}",
    f"/report-vendor/./{_V4_LWC}",
    f"/report-vendor/../vendor/{_V4_LWC}",
    f"/report-js/../vendor/{_V4_LWC}",
    f"/report-css/../vendor/{_V4_LWC}",
    f"/vendor/{_V4_LWC}",
    f"/{_V4_LWC}",
])
def test_v4_lwcへはどの経路でも到達できない(display, path: str) -> None:
    base, _app = display
    status, body, _h = _request(base, path)
    assert status == 404
    assert b"Lightweight Charts" not in body


def test_vendor根の他のファイルは配信されない(display) -> None:
    """allowlist に無いものは、実在しても 404（ディレクトリ露出をしない）。"""
    base, _app = display
    assert (_REPORT_WEB / "vendor" / _V4_LWC).is_file()  # 実在することを実証してから
    status, _body, _h = _request(base, f"{REPORT_VENDOR_PREFIX}/{_V4_LWC}")
    assert status == 404


@pytest.mark.parametrize("path", [
    "/report-vendor",
    "/report-vendor/",
    "/report-vendorx.js",
    "/report-vendor/../../../etc/passwd",
])
def test_vendor根のindexと接頭辞共有は404(display, path: str) -> None:
    base, _app = display
    status, _body, _h = _request(base, path)
    assert status == 404


# --- 3. allowlist は 1 ファイル（不変条件 3・構造での担保）-------------------

def test_合成根のallowlistはchart_umd_jsだけ(display) -> None:
    _base, app = display
    assert REPORT_VENDOR_ALLOWED == frozenset({"/chart.umd.js"})
    vendor_route = app.static_server.route(REPORT_VENDOR_PREFIX)
    assert vendor_route.allowed == REPORT_VENDOR_ALLOWED


def test_子文書はChartjsを載せv4_lwcは載せない(display) -> None:
    """結線は端から端まで（受け口だけ作っても front が読まなければ無言で死ぬ）。"""
    base, _app = display
    status, body, _h = _request(base, "/report_view.html")
    assert status == 200
    assert b"/sim/report-vendor/chart.umd.js" in body
    assert b"standalone.js" not in body


# --- 4. 既存面は不変（不変条件 4）-------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/report-js/chart.js", 200),
    ("/report-css/style.css", 200),
    ("/vendor/lightweight-charts.js", 200),
    ("/index.html", 200),
    ("/report-jsx.js", 404),
])
def test_既存の配信面は変わらない(display, path: str, expected: int) -> None:
    base, _app = display
    status, _body, _h = _request(base, path)
    assert status == expected
