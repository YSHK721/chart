"""serve_sim_ea_series（ea_name 別 registry 系列一覧 API つき sim core）の結合検定（Phase 6 F-8）.

固定する不変条件（名前空間結線の抜本解決・依頼者承認 2026-08-12）:
    1. `GET /ea-series/{ea_name}` が **その EA の registry 系列名**（build_ea_indicators 単一
       ソース）を 200 で返す。PRO_fit_Band_EA には ema/adx/close 等が入る（front の候補源が
       submit_job E-5・GenericConditionStrategy と同一名前空間になることの実証）。
    2. 複数 EA で ea_name 別に系列が変わる（TC24051901 は close/madiff）。
    3. ea_name セグメント不在は 400。
    4. 既存の配信面・API 面（/indicators・/report-js/*・静的）は 1 バイトも変わらない
       （委譲で包む＝OCP・Phase 3/4/5 の応答と byte 一致）。
    5. 接頭辞を共有する別パス（/ea-series-extra）は既存の静的面へ落ちる（prefix 境界）。

方式: 実 HTTP（port=0）・実 EaRegistrySeriesCatalog（合成 CSV で factory を探索・marketdata
実体に触れない）。既存 `test_serve_sim_display.py` と同方式。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from simulator.sim_ui.framework.serve_sim_display import make_server
from simulator.sim_ui.main.composition_root_display import build_sim_display_app
from simulator.sim_ui.main.composition_root_indicators import build_sim_indicator_app

_ROOT = Path(__file__).resolve().parents[4]
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
        with urllib.request.urlopen(req, timeout=10) as r:
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
    """比較対象（ea-series 層なしの Phase 3 core）。既存面の byte 不変を突き合わせる。"""
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


# --- 1/2. ea_name 別 registry 系列（不変条件 1/2）----------------------------

def test_pro_fit_band_series_are_registry_names(display) -> None:
    base, _app = display
    status, body, headers = _request(base, "/ea-series/PRO_fit_Band_EA")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["ea_name"] == "PRO_fit_Band_EA"
    # E-5 / GenericConditionStrategy が参照するのと同一の registry 系列名
    for name in ("ema", "adx", "close"):
        assert name in payload["series"], payload["series"]
    # 因果カタログ名前空間（MA など）ではない
    assert "MA" not in payload["series"]


def test_tc_series_differ_by_ea_name(display) -> None:
    base, _app = display
    _s, body, _h = _request(base, "/ea-series/TC24051901")
    payload = json.loads(body)
    assert set(payload["series"]) == {"close", "madiff"}


def test_series_are_sorted(display) -> None:
    base, _app = display
    _s, body, _h = _request(base, "/ea-series/PRO_fit_Band_EA")
    series = json.loads(body)["series"]
    assert series == sorted(series)


# --- 3. ea_name 不在は 400（不変条件 3）--------------------------------------

@pytest.mark.parametrize("path", ["/ea-series", "/ea-series/"])
def test_missing_ea_name_is_400(display, path: str) -> None:
    base, _app = display
    status, _body, _h = _request(base, path)
    assert status == 400


# --- 4. 既存面は byte 不変（不変条件 4・OCP）---------------------------------

@pytest.mark.parametrize("path", [
    "/",
    "/index.html",
    "/indicators",
    "/jobs",
    "/js/does-not-exist.js",
    "/vendor/lightweight-charts.js",
])
def test_existing_faces_are_byte_identical(display, indicators, path: str) -> None:
    base, _app = display
    d_status, d_body, _dh = _request(base, path)
    i_status, i_body, _ih = _request(indicators, path)
    assert (d_status, d_body) == (i_status, i_body)


# --- 5. prefix 境界（不変条件 5）--------------------------------------------

def test_prefix_neighbor_falls_to_static(display) -> None:
    base, _app = display
    status, _body, _h = _request(base, "/ea-series-extra")
    assert status == 404


def test_controller_is_wired(display) -> None:
    """委譲で属性が解決される（結線の複製 0・ISSUE-291 防止）。"""
    _base, app = display
    assert app.ea_series_controller is not None
    assert app.indicator_controller is not None  # 内側の面も委譲で生きている
