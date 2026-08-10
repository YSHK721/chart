"""report_ui の E2E 検証で共有する起動ハーネス（ISSUE-314）。

6 本の verify_*.py が「空きポートを取る／キャッシュ無効の静的サーバを立てる／Playwright で
開いて ``window.__READY`` を待つ」という同一の手順を 1 文字も違わず複製していた
（codescan 実測: `_free_port` 6 箇所・`_serve` 6 箇所・`_launch` 4 箇所）。手順は 1 つなので
ここに 1 つだけ置く。

各 verify_*.py が持ち続けるもの: 配信する web root の組み立て（``_build_web_root``）。
これは検証したいシナリオそのもの（ダミー report.json の中身）であり、ファイルごとに異なる。
"""
from __future__ import annotations

import http.server
import socket
import threading
from pathlib import Path
from typing import Any, Callable

import pytest


def free_port() -> int:
    """使用可能な TCP ポートを 1 つ取る。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """``Cache-Control: no-store`` を付け、アクセスログを出さない静的ハンドラ。"""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


def serve(directory: str, port: int) -> http.server.HTTPServer:
    """``directory`` を ``port`` で配信するデーモンスレッドのサーバを起動して返す。"""
    handler = lambda *a, **k: NoCacheHandler(*a, directory=directory, **k)  # noqa: E731
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def launch(build_web_root: Callable[[Path], Path], tmp_path: Path) -> "tuple[Any, Any, Any, Any]":
    """web root を組み立てて配信し、chromium で開いて ``window.__READY`` まで待つ。

    Playwright・chromium が無い環境では :func:`pytest.skip` する（既存 verify.py の規約）。

    Args:
        build_web_root: ``tmp_path`` を受けて配信対象ディレクトリを返す（呼出側が持つシナリオ）。
        tmp_path: pytest の一時ディレクトリ。

    Returns:
        ``(playwright, browser, page, httpd)``。後片付けは呼出側の責務（従来と同じ）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright 未導入")
    root = build_web_root(tmp_path)
    port = free_port()
    httpd = serve(str(root), port)
    p = sync_playwright().start()
    try:
        browser = p.chromium.launch()
    except Exception:
        httpd.shutdown()
        p.stop()
        pytest.skip("chromium 未導入")
    page = browser.new_page()
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait_for_function("window.__READY === true", timeout=8000)
    return p, browser, page, httpd
