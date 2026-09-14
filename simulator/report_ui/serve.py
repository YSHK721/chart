#!/usr/bin/env python3
"""no-cache 静的配信サーバ（詳細設計 §3.4・試作 serve.py 同型）。

document root = simulator/report_ui/web/。report.json のキャッシュ起因の表示不具合を排除する。
ローカル開発用途（localhost バインド・read-only 配信）。
"""
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
WEB = Path(__file__).parent / "web"


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    os.chdir(WEB)
    print(f"serving {WEB} on :{PORT} (no-cache)")
    HTTPServer(("127.0.0.1", PORT), NoCacheHandler).serve_forever()
