"""serve_reports — out/ のレポート HTML を charset 明示で配信する（ISSUE-369）。

`python -m http.server` は Content-Type に charset を付けないため、ブラウザの既定
エンコーディング設定によっては UTF-8 の日本語が文字化けしうる。本スクリプトは
text 系の応答へ `; charset=utf-8` を必ず付ける。

使い方:
    python serve_reports.py [port]     # 既定 8890・foreground・Ctrl-C で停止
起動時に閲覧 URL を必ず表示する（どこに何が出たかを人が覚えない）。
"""

from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_OUT = Path(__file__).resolve().parent / "out"


class Utf8Handler(SimpleHTTPRequestHandler):
    """text 系 Content-Type に charset=utf-8 を明示するハンドラ。"""

    def guess_type(self, path):  # noqa: N802 (基底クラスの命名に従う)
        base = super().guess_type(path)
        if isinstance(base, str) and base.startswith("text/") and "charset" not in base:
            return base + "; charset=utf-8"
        return base


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8890
    handler = partial(Utf8Handler, directory=str(_OUT))
    # ThreadingHTTPServer 必須: 非スレッド版はブラウザの投機的プリコネクト（無通信の
    #   keep-alive 接続）1 本で serve_forever が塞がり、全リクエストが無応答になる
    #   （2026-08-11 実測: プロセス生存・curl 000 の原因）。
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"レポート配信を開始: http://localhost:{port}/（停止は Ctrl-C）")
    for f in sorted(_OUT.glob("*.html")):
        if not f.name.startswith("artifact_"):
            print(f"  http://localhost:{port}/{f.name}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
