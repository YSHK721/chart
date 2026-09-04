"""serve_sim の HTTP エンドポイントテスト（Phase 1 = 静的配信のみ）。

対象: `simulator/sim_ui/framework/serve_sim.py` の `SimApp` / `make_server`。
方式: 実データ非依存。tmp_path に web 根を作り、実 HTTP で配信面と防御を検証する。

Phase 1 の sim コアが持つ API は静的配信だけである（ジョブ API は Phase 2 = F-3）。
検証点は 3 つ:
  1. `/` が index.html を返す（疎通）
  2. 不在パスは 404
  3. パストラバーサル（`..` / web 根外を指す symlink）は配信しない（CWE-22）

3 は `simulator.replay_ui.framework.static_file_server.StaticFileServer` を **import で再利用**
していることの behavioral な実証でもある。防御を複製すると、片方だけ直る／片方だけ腐るという
形で必ず食い違う（実測: ルータ側は同型の弱点を長く回帰テスト無しで持っていた）。

構造は AAA。テスト名は「対象_条件_期待結果」。
"""
from __future__ import annotations

import threading
import urllib.request
from urllib.error import HTTPError

import pytest

from simulator.sim_ui.framework.serve_sim import SimApp, make_server


@pytest.fixture
def sim_web(tmp_path):
    """sim の web 根と、その**外**に置いた機密（逸脱検証用）を用意する。"""
    web = tmp_path / "sim_web"
    (web / "js").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>sim</title>", encoding="utf-8")
    (web / "js" / "boot.js").write_text("export const ok = 1;\n", encoding="utf-8")
    # 区切り境界を見ない prefix 一致なら通過しうる「接頭辞を共有する兄弟」。
    secret = tmp_path / "sim_web_SECRET"
    secret.mkdir()
    (secret / "leak.txt").write_text("TOP_SECRET", encoding="utf-8")
    # rel に `..` を含まないまま根の外へ出る唯一の経路（realpath ガードの検証点）。
    (web / "link").symlink_to(secret, target_is_directory=True)
    return web, secret


@pytest.fixture
def server_base(sim_web):
    web, _secret = sim_web
    app = SimApp(web_dir=web, shared_js_root=None)
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, r.read(), dict(r.headers)


def test_root_returns_index_html_with_200(server_base):
    # Arrange / Act
    status, body, headers = _get(server_base, "/")
    # Assert
    assert status == 200
    assert b"<!doctype html>" in body.lower()
    assert headers["Content-Type"].startswith("text/html")
    # 古い ES モジュールを掴ませない（両 core と同一方針）。
    assert headers["Cache-Control"] == "no-store"


def test_static_js_is_served_with_javascript_content_type(server_base):
    # Arrange / Act
    status, body, headers = _get(server_base, "/js/boot.js")
    # Assert
    assert status == 200
    assert b"export const ok" in body
    assert headers["Content-Type"].startswith("application/javascript")


def test_missing_path_returns_404(server_base):
    # Arrange / Act / Assert
    with pytest.raises(HTTPError) as ei:
        _get(server_base, "/does-not-exist.js")
    assert ei.value.code == 404


@pytest.mark.parametrize("path", [
    "/../sim_web_SECRET/leak.txt",            # 生 `..`
    "/js/../../sim_web_SECRET/leak.txt",      # 深い階層からの逸脱
    "/link/leak.txt",                         # 根の外を指す symlink（`..` を含まない経路）
])
def test_path_traversal_is_rejected_with_404(server_base, path):
    """web 根の外は配信しない（CWE-22）。防御は replay の StaticFileServer が単一ソース。"""
    # Arrange / Act
    try:
        status, body, _ = _get(server_base, path)
    except HTTPError as exc:
        status, body = exc.code, exc.read()
    # Assert
    assert b"TOP_SECRET" not in body, "機密が漏洩した"
    assert status == 404, f"逸脱要求が {status} で通過している"


def test_static_defense_is_the_shared_single_source_not_a_copy():
    """静的解決は replay の StaticFileServer の**実体**である（複製禁止・§11.4）。"""
    # Arrange
    from simulator.replay_ui.framework.static_file_server import StaticFileServer
    app = SimApp(web_dir=None, shared_js_root=None)
    # Act / Assert
    assert isinstance(app.static_server, StaticFileServer)


def test_shared_js_root_falls_back_when_web_dir_misses(tmp_path):
    """web_dir で miss した資産は shared_js_root の js/ から配信する（replay と同型）。"""
    # Arrange
    web = tmp_path / "sim_web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html>", encoding="utf-8")
    shared = tmp_path / "indicator_web"
    (shared / "js").mkdir(parents=True)
    (shared / "js" / "shared.js").write_text("export const s = 1;\n", encoding="utf-8")
    app = SimApp(web_dir=web, shared_js_root=shared)
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        # Act
        status, body, _ = _get(f"http://127.0.0.1:{port}", "/js/shared.js")
        # Assert
        assert status == 200
        assert b"export const s" in body
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)
