"""dashboard core（127.0.0.1:8481）の殻が正しく結線されているかを実 HTTP で確認する。

`indigators/indicator_ui/api/tests/test_server_smoke.py` と同型: エフェメラルポートで
`ThreadingHTTPServer` を立て、`urllib.request` で叩く（fake を挟まない・stdlib のみ）。
純ロジック（`p` の算出・ラダー・到達判定）は単体で網羅済みなので、ここで見るのは
**結線**である: Composition Root が束ねた口が実データで応答するか。

`unified_ui/serve.sh` は `GET /` が 200 を返すまで待ってから router を起動する
（`wait_up`）。したがって `GET /` は web/ が未実装でも 200 でなければならない。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from dashboard_ui.framework.serve_dashboard import make_server
from dashboard_ui.main.composition_root import build_dashboard_app

REF = "jp225_tick"

#: 実データで叩く最小の束（1m の 2 本だけ。速度のために本数を絞る）。
INSTANCES = [
    {"instance_id": "ma-24", "indicator_id": "moving_averages", "variant": "default",
     "params": {"source": "hlc3", "ma_type": "ema", "length": 24}},
    {"instance_id": "marod", "indicator_id": "ma_marod", "variant": "default",
     "params": {"source": "hlc3", "ma_type": "ema", "length": 50}},
]


@pytest.fixture(scope="module")
def base() -> str:
    server = make_server(build_dashboard_app(bar_limits={"1m": 600}), port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(base: str, path: str, payload) -> "tuple[int, dict]":
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, json.loads(body) if body else {}


def get(base: str, path: str) -> "tuple[int, str, bytes]":
    try:
        with urllib.request.urlopen(base + path, timeout=30) as response:
            return (response.status, response.headers.get("Content-Type", ""),
                    response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def test_the_root_answers_so_that_serve_sh_can_wait_for_it(base: str) -> None:
    status, content_type, body = get(base, "/")

    assert status == 200
    assert "text/html" in content_type
    assert body


def test_the_reach_sheet_answers_with_real_material(base: str) -> None:
    status, response = post(base, "/reach_sheet", {
        "dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
        "instances": INSTANCES,
    })

    assert status == 200
    assert response["ok"] is True
    assert response["current_price"] > 0.0
    assert response["rows"]
    assert response["cells"]


def test_the_rows_are_sorted_by_price_and_split_at_the_current_price(base: str) -> None:
    """並び替えはサーバ側で終わっている（フロントは再計算しない）。"""
    _status, response = post(base, "/reach_sheet", {
        "dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
        "instances": INSTANCES,
    })

    prices = [row["price"] for row in response["rows"]]
    index = response["current_index"]

    assert prices == sorted(prices, reverse=True)
    assert all(price > response["current_price"] for price in prices[:index])


def test_the_oscillator_cell_comes_back_with_its_reach_state(base: str) -> None:
    _status, response = post(base, "/reach_sheet", {
        "dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
        "instances": INSTANCES,
    })

    cells = [cell for cell in response["cells"] if cell["indicator_id"] == "ma_marod"]

    assert len(cells) == 1
    assert cells[0]["value"] is not None
    assert set(cells[0]["reach"]) == {"reached", "since_time", "truncated"}


def test_an_unknown_dataset_is_reported_as_a_failure(base: str) -> None:
    status, response = post(base, "/reach_sheet", {
        "dataset_ref": "nope", "chart_timeframe": "1m", "instances": INSTANCES,
    })

    assert status == 400
    assert response["ok"] is False
    assert response["error"]["message"]


def test_a_broken_body_is_reported_as_a_failure(base: str) -> None:
    status, response = post(base, "/reach_sheet", b"{not json")

    assert status == 400
    assert response["ok"] is False
    assert response["error"]["type"] == "validation"


def test_an_unknown_endpoint_is_not_served(base: str) -> None:
    status, _response = post(base, "/nope", {})

    assert status == 404


def test_a_path_traversal_is_not_served(base: str) -> None:
    """配信面の外へ抜けられない（CWE-22。防御は共有の StaticFileServer が持つ）。"""
    status, _content_type, _body = get(base, "/../../etc/passwd")

    assert status == 404
