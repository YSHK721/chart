"""serve_sim — シミュレーション（バックテスト）UI バックエンドの HTTP フレームワーク層。

エンドポイント（Phase 1）:
    GET  /（静的）  → web_dir 配信（shared_js_root フォールバックつき・no-store）

Phase 1 の sim コアが持つのは**静的配信だけ**である。ジョブ API（`POST /sim/jobs` 等・
基本設計書 §6.1）は Phase 2（F-3）で、ジョブ状態遷移という実在の変更要因とともに足す
（§11.4 YAGNI: 先に空の口を作らない）。

CLEAN_ARCH §6: HTTP・スレッド・静的配信という偶有的技術を最外層へ隔離する
（`serve_replay.py` と同型の ThreadingHTTPServer 構成）。

静的解決とパストラバーサル防御は `simulator.replay_ui.framework.static_file_server` を
**import で再利用**する（§11.4: sim 専用ラッパを作らない）。CWE-22 の防御を複製すると、
片方だけ直る／片方だけ腐るという形で必ず食い違う。防御は単一ソースに閉じる。
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 静的資産配信＋パストラバーサル防御の単一ソース（複製禁止・§11.4）。
from simulator.replay_ui.framework.static_file_server import StaticFileServer


class SimApp:
    """sim コアのアプリケーション面（framework 層）。

    ``web_dir``: sim フロントの配信根（`simulator/sim_ui/web`）。None で静的配信無効。
    ``shared_js_root``: 単一ソース共有のフォールバック根（既定は Composition Root が
      `<repo>/indigators/indicator_ui/web` を渡す）。配信を許可するのは本根の
      ``js/`` ``css/`` ``vendor/`` サブツリーのみ（最小権限＝StaticFileServer の規約）。
    """

    def __init__(self, *, web_dir: Any = None, shared_js_root: Any = None) -> None:
        # cwd 非依存の絶対パスへ解決する（起動場所で配信面が変わらないようにする）。
        self.web_dir = Path(web_dir).resolve() if web_dir else None
        self.shared_js_root = Path(shared_js_root).resolve() if shared_js_root else None
        self.static_server = StaticFileServer(self.web_dir, self.shared_js_root)


def make_handler(app: SimApp):
    """``app`` を束ねた BaseHTTPRequestHandler サブクラスを返す（serve_replay と同型）。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401 — アクセスログ抑制（両 core と同一）
            pass

        def do_GET(self):  # noqa: N802
            # Phase 1 は静的配信のみ。解決・許可根・トラバーサル防御は StaticFileServer へ委譲する。
            return app.static_server.serve(self, urlparse(self.path).path)

    return Handler


def make_server(app: SimApp, host: str = "127.0.0.1", port: "int | None" = None) -> ThreadingHTTPServer:
    """サーバを生成して返す（起動はしない）。``port=None`` は空きポート（テスト用）。"""
    return ThreadingHTTPServer((host, port or 0), make_handler(app))


def serve(app: SimApp, host: str = "127.0.0.1", port: "int | None" = None) -> None:
    """サーバを起動して待ち受ける（ブロッキング）。"""
    server = make_server(app, host, port)
    actual = server.server_address[1]
    print(f"sim backend: http://{host}:{actual}/  (Ctrl-C 停止)")
    server.serve_forever()
