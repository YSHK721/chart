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

import threading
import urllib.request
from urllib.error import HTTPError

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

    def load_ticks(self, start, end):
        return []


@pytest.fixture
def overlay_ctx(tmp_path):
    """replay web_dir と shared_js_root を実ファイルで用意して server を起動する。"""
    # web_dir は replay の web 根（index.html + js/ を含む）。shared_js_root は indicator_ui の web/js。
    web = tmp_path / "replay_web"
    shared = tmp_path / "shared_js"
    (web / "js" / "adapter" / "front").mkdir(parents=True)
    (shared / "adapter" / "front").mkdir(parents=True)

    # replay 固有ファイル（shared には無い）。
    (web / "js" / "replay.js").write_text("REPLAY_SPECIFIC", encoding="utf-8")
    # 双方に在るファイル（replay 優先＝挙動不変を検証）。
    (web / "js" / "adapter" / "front" / "chart_renderer.js").write_text(
        "REPLAY_RENDERER", encoding="utf-8")
    (shared / "adapter" / "front" / "chart_renderer.js").write_text(
        "SHARED_RENDERER", encoding="utf-8")
    # 共有のみ（replay に無い→フォールバック配信）。
    (shared / "adapter" / "front" / "forming_bar_updater.js").write_text(
        "SHARED_ONLY_FALLBACK", encoding="utf-8")
    # 単一ソース symlink（web_dir/js 配下から shared_js_root の実体を指す）。resolve() 後は
    #   shared_js_root 配下に落ちるため、dual-root ガードでのみ 200 配信される（web_dir 単独
    #   ガードだと弾かれる）。symlink 名は shared 実体名と異なる（名前一致フォールバックでは
    #   解決できない＝一次解決の dual-root ガードを実証する）。
    (shared / "adapter" / "front" / "shared_impl.js").write_text(
        "SHARED_VIA_SYMLINK", encoding="utf-8")
    (web / "js" / "adapter" / "front" / "linked_view.js").symlink_to(
        shared / "adapter" / "front" / "shared_impl.js")

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


def test_path_traversal_blocked_on_both_routes(overlay_ctx):
    with pytest.raises(HTTPError) as ei:
        _get_text(overlay_ctx, "/js/../../../../etc/passwd")
    assert ei.value.code == 404


def test_missing_file_returns_404(overlay_ctx):
    with pytest.raises(HTTPError) as ei:
        _get_text(overlay_ctx, "/js/does_not_exist_anywhere.js")
    assert ei.value.code == 404
