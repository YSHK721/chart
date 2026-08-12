"""serve_sim_display（表示層の配信面つき sim core・framework/main）の結合検定（Phase 4 F-10）。

固定する不変条件（Phase 4 実装指示書 §新規ファイル F-5/F-6/F-7・検定 3 層の層 2）:
    1. `GET /report-js/*` が report_ui の **JS 実体そのもの**を 200 で返す（sim 側へ写しを
       置かない＝複製 0 の配信経路）。
    2. `GET /report-css/style.css` が report_ui の CSS 実体を 200（`text/css`）で返す。
    3. **report_ui の vendor（lightweight-charts v4.1.3）はどの route にも載らない**
       （NFR-07 の構造担保）。v4 バンドルへ到達しようとする経路はすべて 404。
       表示層が使う vendor は共有根の v5.2.0 だけである。
    4. **既存の配信面・API 面は 1 バイトも変わらない**（Phase 1〜3 と同一）。
       `SimIndicatorApp` の応答と status・本文 byte を突き合わせて実証する。
    5. パストラバーサル防御（CWE-22）は追加した根でも効く。
    6. 追加 prefix と接頭辞を共有する別パス（`/report-jsx.js`）は既存の静的面へ落ちる。

方式: 実 HTTP（port=0 の空きポート）。既存 `test_serve_sim_indicators.py` と同方式。
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from simulator.sim_ui.framework.serve_sim_display import SimDisplayApp, make_server
from simulator.sim_ui.main.composition_root_display import (
    REPORT_CSS_PREFIX,
    REPORT_JS_PREFIX,
    REPORT_VENDOR_PREFIX,
    build_sim_display_app,
)
from simulator.sim_ui.main.composition_root_indicators import build_sim_indicator_app

_ROOT = Path(__file__).resolve().parents[4]
_REPORT_WEB = _ROOT / "simulator" / "report_ui" / "web"
_SIM_WEB = _ROOT / "simulator" / "sim_ui" / "web"


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


@pytest.fixture
def indicators(tmp_path: Path):
    """比較対象（Phase 3 の core）。既存面の byte 不変を突き合わせるために立てる。

    ``data_root`` は `display` と**同一**にする。固定したい不変条件は「包んでも応答が
    変わらない」であり、それは同一構成どうしの比較でしか言えない。別の根を渡すと、
    根の絶対パスを本文へ載せる応答（台帳不在の 503）が構成差で食い違い、包み手の
    無影響性とは無関係な理由で落ちる（実測: `/indicators` の 503 本文にパスが載る）。
    """
    app = build_sim_indicator_app(
        repo_root=_ROOT, web_dir=_SIM_WEB, data_root=tmp_path / "data"
    )
    srv, thread, base = _serve(app)
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


# --- 1. report_ui JS の配信（不変条件 1）------------------------------------

@pytest.mark.parametrize(
    "name", ["chart.js", "linkage.js", "table.js", "format.js"]
)
def test_report_jsが実体そのままで返る(display, name: str) -> None:
    base, _app = display
    status, body, headers = _request(base, f"{REPORT_JS_PREFIX}/{name}")
    assert status == 200
    assert body == (_REPORT_WEB / "js" / name).read_bytes()
    assert headers["Content-Type"] == "application/javascript; charset=utf-8"


def test_report_jsの不在ファイルは404(display) -> None:
    base, _app = display
    status, _body, _h = _request(base, f"{REPORT_JS_PREFIX}/does-not-exist.js")
    assert status == 404


# --- 2. report_ui CSS の配信（不変条件 2）------------------------------------

def test_report_cssが実体そのままで返る(display) -> None:
    base, _app = display
    status, body, headers = _request(base, f"{REPORT_CSS_PREFIX}/style.css")
    assert status == 200
    assert body == (_REPORT_WEB / "css" / "style.css").read_bytes()
    assert headers["Content-Type"] == "text/css; charset=utf-8"


# --- 3. v4 vendor はどこにも載らない（不変条件 3・NFR-07）--------------------

@pytest.mark.parametrize("path", [
    "/report-js/../vendor/lightweight-charts.standalone.js",
    "/report-css/../vendor/lightweight-charts.standalone.js",
    "/report-vendor/lightweight-charts.standalone.js",
    "/vendor/lightweight-charts.standalone.js",
    "/vendor/chart.umd.js",
])
def test_v4バンドルへは到達できない(display, path: str) -> None:
    base, _app = display
    status, body, _h = _request(base, path)
    assert status == 404
    assert b"Lightweight Charts" not in body


def test_合成根はvendor根をどのrouteにも載せない(display) -> None:
    """構造での担保（経路が増えても v4 が露出しない）。"""
    _base, app = display
    assert set(app.static_server.prefixes) == {
        REPORT_JS_PREFIX, REPORT_CSS_PREFIX, REPORT_VENDOR_PREFIX}


def test_子文書と器CSSが配信される(display) -> None:
    """裁定 B/C: 表示の実体は子文書、統合ページへ入る CSS は器の寸法 1 枚だけ。

    どちらも sim の web 根に置くので既存の静的面が配信する（route を増やさない）。
    """
    base, _app = display
    status, body, headers = _request(base, "/report_view.html")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"/sim/report-css/style.css" in body
    status, body, headers = _request(base, "/css/sim_display.css")
    assert status == 200
    assert headers["Content-Type"] == "text/css; charset=utf-8"
    assert b"#sim-display" in body


def test_子文書はv4バンドルを載せない(display) -> None:
    base, _app = display
    _status, body, _h = _request(base, "/report_view.html")
    assert b"standalone.js" not in body
    assert b"/sim/vendor/lightweight-charts.js" in body


def test_共有vendorのv5は従来どおり配信される(display) -> None:
    """表示層が使う vendor は共有根の v5.2.0 だけ（R-P4-1/2 の実測点）。"""
    base, _app = display
    status, body, _h = _request(base, "/vendor/lightweight-charts.js")
    assert status == 200
    assert b"v5.2.0" in body[:400]


# --- 4. 既存面は byte 不変（不変条件 4）--------------------------------------

@pytest.mark.parametrize("path", [
    "/",
    "/index.html",
    "/indicators",
    "/jobs",
    "/js/does-not-exist.js",
    "/report-jsx.js",
    "/vendor/lightweight-charts.js",
])
def test_既存面の応答はPhase3とbyte一致(display, indicators, path: str) -> None:
    base, _app = display
    d_status, d_body, _dh = _request(base, path)
    i_status, i_body, _ih = _request(indicators, path)
    assert (d_status, d_body) == (i_status, i_body)


def test_静的配信は従来どおり(display) -> None:
    base, _app = display
    status, body, _h = _request(base, "/")
    assert status == 200
    assert b"<!doctype html>" in body.lower()


def test_指標一覧の口は生きている(display) -> None:
    """台帳不在の 503（fail-closed）。委譲で包んでも JSON ルートが死なない。"""
    base, _app = display
    status, _body, _h = _request(base, "/indicators")
    assert status == 503


def test_内側のアプリ属性が委譲で解決される(display) -> None:
    """Handler は `app.controller` / `app.result_server` を属性で引く（ISSUE-291 防止）。"""
    _base, app = display
    assert app.controller is not None
    assert app.result_server is not None
    assert app.web_dir is not None
    assert app.indicator_controller is not None


# --- 5. パストラバーサル防御（不変条件 5）------------------------------------

@pytest.mark.parametrize("path", [
    "/report-js/../../../etc/passwd",
    "/report-css/../js/chart.js",
    "/../simulator/report_ui/web/vendor/lightweight-charts.standalone.js",
])
def test_パストラバーサル防御は追加根でも効く(display, path: str) -> None:
    base, _app = display
    status, _body, _h = _request(base, path)
    assert status == 404


# --- 6. prefix 境界（不変条件 6）---------------------------------------------

@pytest.mark.parametrize("path", ["/report-jsx.js", "/report-cssx.css"])
def test_接頭辞を共有する別パスは静的面へ落ちる(display, path: str) -> None:
    base, _app = display
    status, _body, _h = _request(base, path)
    assert status == 404


def test_表示層アプリは委譲で包んでいる(display) -> None:
    """継承で Handler を積み上げていない（結線の複製 0）。"""
    _base, app = display
    assert isinstance(app, SimDisplayApp)
    assert app.inner is not None
