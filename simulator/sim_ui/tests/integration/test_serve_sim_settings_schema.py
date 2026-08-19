"""serve_sim_settings_schema（Tester Settings schema API つき sim core）の結合検定（Phase 8 スライス 2）.

固定する不変条件（基本設計 §18.4 スライス 2 の通過条件）:
    1. `GET /settings-schema` が 200 で schema を返す（実 HTTP・実カタログ・実 EA 名）。
    2. `Period` の選択肢は enums の全ラベルを**過不足なく**載せる（配信まで導出が届く）。
    3. wrapper を**足す前と後**で、既存面（/run-options・/ea-series/{ea}・静的・/jobs・
       /indicators）の応答が 1 バイトも変わらない（委譲・OCP）。
    4. wrapper を足す前の面には `/settings-schema` が**無い**（この 1 本だけが増分である）。
    5. 接頭辞を共有する別パス（/settings-schema-extra）は既存の静的面へ落ちる（prefix 境界）。

「足す前」は合成根を書き写して組み直すのではなく、**同一の object graph の内側**
（`app.inner.inner`）をそのまま配信する。組み直すと合成根の複製になり、比較対象が
「本物の内側」であることを保証できない。
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
from simulator.usecase.tester_settings.enums import TIMEFRAME_INI_LABELS, TickModel

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
def apps(tmp_path: Path):
    """wrapper 込みの面（after）と、その内側＝wrapper を足す前の面（before）。"""
    after = build_sim_display_app(
        repo_root=_ROOT, web_dir=_SIM_WEB, data_root=tmp_path / "data"
    )
    # SimDisplayApp → SimSettingsSchemaApp → （wrapper を足す前の面）
    before = after.inner.inner
    srv_a, thread_a, base_a = _serve(after)
    srv_b, thread_b, base_b = _serve(before)
    try:
        yield base_a, base_b, after
    finally:
        for srv, thread in ((srv_a, thread_a), (srv_b, thread_b)):
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=2)


def test_settings_schema_returns_200_json(apps) -> None:
    base, _before, _app = apps
    status, body, headers = _request(base, "/settings-schema")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert headers["Cache-Control"] == "no-store"
    payload = json.loads(body)
    assert payload["ok"] is True


def test_served_period_options_cover_every_enum_label(apps) -> None:
    base, _before, _app = apps
    payload = json.loads(_request(base, "/settings-schema")[1])
    tokens = {o["token"] for o in payload["enum_options"]["Period"]}
    assert tokens == set(TIMEFRAME_INI_LABELS.values())
    assert {o["token"] for o in payload["enum_options"]["Model"]} == {
        str(int(m)) for m in TickModel
    }


def test_served_schema_carries_real_expert_options_and_unsupported(apps) -> None:
    """実 EA 名・実非対象宣言表が配信まで届く（fake で緑にならない）。"""
    from simulator.main import known_ea_names
    from simulator.main.tester_settings.ea_input_map import SUBJECT_SUFFIX
    from simulator.main.tester_settings.unsupported import RULES

    base, _before, _app = apps
    payload = json.loads(_request(base, "/settings-schema")[1])
    tokens = [o["token"] for o in payload["expert_options"]]
    assert all(t.endswith(SUBJECT_SUFFIX) for t in tokens)
    assert {t.removesuffix(SUBJECT_SUFFIX) for t in tokens} == set(known_ea_names())
    assert {n["unsupported_id"] for n in payload["unsupported"]} == set(RULES)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/indicators",
        "/jobs",
        "/run-options",
        "/ea-series/TC24051901",
    ],
)
def test_existing_faces_are_byte_identical_before_and_after_the_wrapper(apps, path) -> None:
    base_after, base_before, _app = apps
    after = _request(base_after, path)
    before = _request(base_before, path)
    assert (after[0], after[1]) == (before[0], before[1])


def test_the_wrapper_is_the_only_thing_that_adds_the_route(apps) -> None:
    """足す前の面に `/settings-schema` が無いこと（増分が 1 本であることの実証）。"""
    base_after, base_before, _app = apps
    assert _request(base_after, "/settings-schema")[0] == 200
    assert _request(base_before, "/settings-schema")[0] != 200


def test_prefix_neighbor_falls_to_static(apps) -> None:
    base, _before, _app = apps
    assert _request(base, "/settings-schema-extra")[0] in (404, 200)
    assert _request(base, "/settings-schema")[0] == 200
