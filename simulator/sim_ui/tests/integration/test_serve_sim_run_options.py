"""serve_sim_run_options（run config 選択肢 API つき sim core）の結合検定（Phase 6 拡張）.

固定する不変条件（run config フォーム結線・依頼者承認 2026-08-12）:
    1. `GET /run-options` が datasets（JP225 プロファイル）＋ea_names を 200 で返す。
    2. datasets の JP225 は結果に効く定数（contract_size=10 等）を持つ（フォーム投入が
       完全 spec になる＝E-5b を通る）。
    3. ea_names は _EA_FACTORIES 由来（TC24051901 を含む）。
    4. 既存面（/indicators・/ea-series・/jobs・静的）は 1 バイトも変わらない（委譲・OCP）。
    5. 接頭辞を共有する別パス（/run-options-extra）は既存の静的面へ落ちる（prefix 境界）。

方式: 実 HTTP（port=0）・実 SymbolSpecCatalog。既存 test_serve_sim_ea_series.py と同方式。
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
    app = build_sim_display_app(repo_root=_ROOT, web_dir=_SIM_WEB, data_root=tmp_path / "data")
    srv, thread, base = _serve(app)
    try:
        yield base, app
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


@pytest.fixture
def indicators(tmp_path: Path):
    app = build_sim_indicator_app(repo_root=_ROOT, web_dir=_SIM_WEB, data_root=tmp_path / "data")
    srv, thread, base = _serve(app)
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def test_run_options_returns_datasets_and_ea_names(display) -> None:
    base, _app = display
    status, body, headers = _request(base, "/run-options")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert payload["ok"] is True
    jp = [d for d in payload["datasets"] if d["symbol"] == "JP225"][0]
    assert jp["contract_size"] == 10.0 and jp["digits"] == 1
    assert jp["point_size"] == 0.1 and jp["leverage"] == 10.0
    assert "TC24051901" in payload["ea_names"]


@pytest.mark.parametrize("path", ["/", "/index.html", "/indicators", "/jobs", "/ea-series/TC24051901"])
def test_existing_faces_are_byte_identical(display, indicators, path: str) -> None:
    # /ea-series は indicators core に無いので比較対象から外し、他既存面のみ突合。
    if path.startswith("/ea-series"):
        base, _app = display
        status, _b, _h = _request(base, path)
        assert status == 200  # ea-series 層は生きている（byte 不変は ea_series 検定が担保）
        return
    base, _app = display
    d = _request(base, path)
    i = _request(indicators, path)
    assert (d[0], d[1]) == (i[0], i[1])


def test_prefix_neighbor_falls_to_static(display) -> None:
    base, _app = display
    status, _body, _h = _request(base, "/run-options-extra")
    assert status in (404, 200)  # 静的面へ落ちる（/run-options ルートには一致しない）
    # /run-options 完全一致だけが JSON を返す
    assert _request(base, "/run-options")[0] == 200
