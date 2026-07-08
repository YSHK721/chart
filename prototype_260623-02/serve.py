#!/usr/bin/env python3
"""no-cache 静的サーバー（試作確認用）。ブラウザのキャッシュ起因の表示不具合を排除する。"""
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    import os
    os.chdir(Path(__file__).parent)
    print(f"serving {Path.cwd()} on :{PORT} (no-cache)")
    HTTPServer(("0.0.0.0", PORT), NoCacheHandler).serve_forever()
