"""serve_replay オーバーレイ静的配信テスト（Phase 1: 単一ソース共有化）。

replay web_dir を優先し、miss（replay に無いファイル）は shared_js_root へフォールバックする。
両ルートに resolve()+startswith ガード（パストラバーサル防御）を適用する。fake port で hermetic。

検証観点:
- replay 固有ファイル（replay.js）は replay web_dir から配信される。
- replay/共有 双方に在るファイル（chart_renderer.js）は replay web_dir が優先される
  （＝複製が残る間は挙動不変・回帰ゼロを保証）。
- replay に無い共有ファイル（forming_bar_updater.js 相当）は shared_js_root フォールバックから配信。
- `..` パストラバーサルは両ルートで 404（resolve+startswith ガード）。
- 双方に無いファイルは 404。
"""
from __future__ import annotations

import socket
import threading
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest

from simulator.replay_ui.framework.serve_replay import ReplayApp, make_server


class _FakeCandlePort:
    def load_candles(self, ref, timeframe, limit):
        return []


class _FakeComputePort:
    def load_source(self, ref, timeframe):
        return []

    def compute(self, indicator, variant, mode, bars, params):
        return []


class _FakeWindowPort:
    def load_m1_rows(self, ref, start, end):
        return []

    def load_raw_ticks(self, start, end):   # ISSUE-031: 生ティック (sec, bid, ask)
        return []


@pytest.fixture
def overlay_ctx(tmp_path):
    """replay web_dir と shared_js_root を実ファイルで用意して server を起動する。"""
    # web_dir は replay の web 根（index.html + js/css/vendor を含む）。shared は indicator_ui の
    #   web 根（js/css/vendor を内包）。共有ルートを web/js から web へ一般化し css/vendor も単一ソース化。
    web = tmp_path / "replay_web"
    shared = tmp_path / "shared_js"
    (web / "js" / "adapter" / "front").mkdir(parents=True)
    (web / "css").mkdir(parents=True)
    (web / "vendor").mkdir(parents=True)
    (shared / "js" / "adapter" / "front").mkdir(parents=True)
    (shared / "css").mkdir(parents=True)
    (shared / "vendor").mkdir(parents=True)

    # replay 固有ファイル（shared には無い）。
    (web / "js" / "replay.js").write_text("REPLAY_SPECIFIC", encoding="utf-8")
    # 双方に在るファイル（replay 優先＝挙動不変を検証）。
    (web / "js" / "adapter" / "front" / "chart_renderer.js").write_text(
        "REPLAY_RENDERER", encoding="utf-8")
    (shared / "js" / "adapter" / "front" / "chart_renderer.js").write_text(
        "SHARED_RENDERER", encoding="utf-8")
    # 共有のみ（replay に無い→フォールバック配信）。
    (shared / "js" / "adapter" / "front" / "forming_bar_updater.js").write_text(
        "SHARED_ONLY_FALLBACK", encoding="utf-8")
    # 単一ソース symlink（web_dir/js 配下から shared 根の実体を指す）。resolve() 後は
    #   shared 根配下に落ちるため、dual-root ガードでのみ 200 配信される（web_dir 単独
    #   ガードだと弾かれる）。symlink 名は shared 実体名と異なる（名前一致フォールバックでは
    #   解決できない＝一次解決の dual-root ガードを実証する）。
    (shared / "js" / "adapter" / "front" / "shared_impl.js").write_text(
        "SHARED_VIA_SYMLINK", encoding="utf-8")
    (web / "js" / "adapter" / "front" / "linked_view.js").symlink_to(
        shared / "js" / "adapter" / "front" / "shared_impl.js")
    # css/vendor の単一ソース symlink（web/js の外＝共有ルートを web へ広げた効果を検証）。
    (shared / "css" / "app.css").write_text("SHARED_CSS_BODY", encoding="utf-8")
    (web / "css" / "app.css").symlink_to(shared / "css" / "app.css")
    (shared / "vendor" / "lib.js").write_text("SHARED_VENDOR_BODY", encoding="utf-8")
    (web / "vendor" / "lib.js").symlink_to(shared / "vendor" / "lib.js")
    # 最小権限回帰: 共有根直下の「非資産」（js/css/vendor 以外）。web 根全体を許可すると配信されて
    #   しまうが、許可根を js/css/vendor サブツリーに限定したため 404 でなければならない。
    (shared / "package.json").write_text("SHOULD_NOT_BE_SERVED", encoding="utf-8")
    (shared / "build.mjs").write_text("SHOULD_NOT_BE_SERVED", encoding="utf-8")
    (shared / "tests").mkdir()
    (shared / "tests" / "x.test.js").write_text("SHOULD_NOT_BE_SERVED", encoding="utf-8")

    # CWE-22 回帰用: web_dir / shared_js_root と「接頭辞を共有する兄弟ディレクトリ」に機密を置く。
    #   区切り境界なしの str.startswith ガードだと `.../replay_web` の prefix を
    #   `.../replay_web_SECRET` が満たすため逸脱できてしまう（境界一致ガードで封じる）。
    secret = tmp_path / "replay_web_SECRET"
    secret.mkdir()
    (secret / "leak.txt").write_text("TOP_SECRET_WEB", encoding="utf-8")
    shared_sibling = tmp_path / "shared_js_SIBLING"
    shared_sibling.mkdir()
    (shared_sibling / "leak.txt").write_text("TOP_SECRET_SHARED", encoding="utf-8")

    app = ReplayApp(
        candle_port=_FakeCandlePort(),
        compute_port=_FakeComputePort(),
        window_port=_FakeWindowPort(),
        web_dir=web,
        shared_js_root=shared,
    )
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


def _get_text(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, r.read().decode(), dict(r.headers)


def _raw_get(base, target):
    """生の request-target（`..` を正規化せず）を送りステータスと本文を返す。

    urllib は URL の `..` をクライアント側で正規化するため、http.server の
    ``_serve_static`` へ生 `..` を到達させる CWE-22 の判別には raw socket を用いる。
    """
    u = urlparse(base)
    s = socket.create_connection((u.hostname, u.port), timeout=5)
    try:
        s.sendall(
            f"GET {target} HTTP/1.1\r\nHost: {u.hostname}\r\n"
            f"Connection: close\r\n\r\n".encode()
        )
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, body


def test_replay_specific_file_served_from_web_dir(overlay_ctx):
    status, body, headers = _get_text(overlay_ctx, "/js/replay.js")
    assert status == 200
    assert body == "REPLAY_SPECIFIC"
    assert headers.get("Cache-Control") == "no-store"
    assert headers.get("Content-Type", "").startswith("application/javascript")


def test_duplicate_file_prefers_replay_web_dir(overlay_ctx):
    # replay/shared 双方に在る → replay 版が配信される（複製が残る間の挙動不変・回帰ゼロ）。
    status, body, _ = _get_text(overlay_ctx, "/js/adapter/front/chart_renderer.js")
    assert status == 200
    assert body == "REPLAY_RENDERER"


def test_shared_only_file_falls_back_to_shared_root(overlay_ctx):
    # replay に無い共有ファイル → shared_js_root フォールバックから配信。
    status, body, headers = _get_text(
        overlay_ctx, "/js/adapter/front/forming_bar_updater.js")
    assert status == 200
    assert body == "SHARED_ONLY_FALLBACK"
    assert headers.get("Content-Type", "").startswith("application/javascript")


def test_web_dir_symlink_to_shared_served_via_dual_root_guard(overlay_ctx):
    # web_dir/js 配下の symlink（→ shared_js_root 実体）を web_dir 経由で一次解決し 200 配信。
    #   resolve() 後は shared_js_root 配下だが dual-root ガードが許可する（単一ソース共有の核）。
    status, body, headers = _get_text(
        overlay_ctx, "/js/adapter/front/linked_view.js")
    assert status == 200
    assert body == "SHARED_VIA_SYMLINK"
    assert headers.get("Content-Type", "").startswith("application/javascript")


def test_css_symlink_served_via_broadened_shared_root(overlay_ctx):
    # web/css/app.css（symlink→shared/css/app.css）を配信。共有ルートを web/js から web へ
    #   広げたことで web/js の外（css）の symlink も境界一致ガードで許可される（単一ソース）。
    status, body, headers = _get_text(overlay_ctx, "/css/app.css")
    assert status == 200
    assert body == "SHARED_CSS_BODY"
    assert headers.get("Content-Type", "").startswith("text/css")


def test_vendor_symlink_served_via_broadened_shared_root(overlay_ctx):
    # web/vendor/lib.js（symlink→shared/vendor/lib.js）を配信（css と同じく web/js 外の単一ソース）。
    status, body, _ = _get_text(overlay_ctx, "/vendor/lib.js")
    assert status == 200
    assert body == "SHARED_VENDOR_BODY"


def test_shared_non_asset_files_not_served(overlay_ctx):
    # 最小権限: 共有根直下の非資産（package.json / build.mjs / tests/*）は js/css/vendor 外なので
    #   404（許可根を web 根全体でなく資産3サブツリーに限定＝least-privilege）。
    for target in ("/package.json", "/build.mjs", "/tests/x.test.js"):
        with pytest.raises(HTTPError) as ei:
            _get_text(overlay_ctx, target)
        assert ei.value.code == 404, f"{target} は配信されてはならない"


def test_path_traversal_blocked_on_both_routes(overlay_ctx):
    with pytest.raises(HTTPError) as ei:
        _get_text(overlay_ctx, "/js/../../../../etc/passwd")
    assert ei.value.code == 404


def test_missing_file_returns_404(overlay_ctx):
    with pytest.raises(HTTPError) as ei:
        _get_text(overlay_ctx, "/js/does_not_exist_anywhere.js")
    assert ei.value.code == 404


def test_prefix_sharing_sibling_of_web_dir_blocked(overlay_ctx):
    # CWE-22 回帰: web_dir=`.../replay_web` と接頭辞を共有する兄弟 `.../replay_web_SECRET`
    #   へ生 `..` で逸脱する exploit。区切り境界なしの startswith ガードでは 200（機密漏洩）に
    #   なるが、境界一致ガードでは 404 でなければならない（修正前 200→修正後 404 の判別力）。
    status, body = _raw_get(overlay_ctx, "/js/../../replay_web_SECRET/leak.txt")
    assert status == 404
    assert b"TOP_SECRET_WEB" not in body


def test_prefix_sharing_sibling_of_shared_root_blocked(overlay_ctx):
    # CWE-22 回帰（shared 側）: shared 根=`.../shared_js` と接頭辞を共有する兄弟
    #   `.../shared_js_SIBLING`（実在する機密）へ生 `..` で逸脱する exploit。full-rel フォールバックで
    #   resolve 先は実在の tmp/shared_js_SIBLING/leak.txt になるが、境界一致ガード（is_relative_to）で
    #   shared 根外＝404 でなければならない（機密非漏洩）。
    status, body = _raw_get(overlay_ctx, "/js/../../shared_js_SIBLING/leak.txt")
    assert status == 404
    assert b"TOP_SECRET_SHARED" not in body
