"""HTTP サーバ殻（framework 層・内部設計書 §2.1 framework/api・§7.3 セキュリティ）。

stdlib のみ（http.server / json / urllib / pathlib）で実装する薄い殻。純ロジックは
adapter 層（``handle_compute`` / ``dataset.load_candles``）に委譲し、本ファイルは
HTTP の入出力・静的配信・パストラバーサル防止のみを担う（新規依存禁止）。

ルート:
  - ``POST /compute``                  : JSON ボディ → handle_compute → (status, dict) を JSON 応答。
  - ``GET  /candles?datasetRef=sample`` : ホワイトリスト解決した candles JSON を応答。未知は 400。
  - ``GET  /...（静的）``               : web/ 配下を same-origin 配信（ES Modules をそのまま読む）。
                                          配信ルートを web/ に限定しパストラバーサルを防ぐ。

セキュリティ（§7.3）:
  - localhost バインドのみ（既定 127.0.0.1）。
  - 本文サイズ上限（_MAX_BODY_BYTES）。超過は 413。
  - 静的配信は web/ ルート内に正規化後パスを限定（``..`` 解決後ルート外なら 404）。
  - 例外時も nested エラーボディ（§6.3.4）で応答する。

依存方向: framework → adapter（handle_compute / dataset）。adapter は read-only で再利用。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# api/ を import パスへ（adapter.* を解決）。conftest と同方針（殻の自己完結起動用）。
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from adapter.compute import dataset  # noqa: E402
from adapter.controller.compute_controller import handle_compute  # noqa: E402

# 静的配信ルート（web/）。api/ → parents[1]=api → parents[2]=indicator_ui → web。
_WEB_ROOT = (_API_ROOT.parent / "web").resolve()

# POST 本文サイズ上限（§7.3・1 MiB）。超過は 413 で拒否する。
_MAX_BODY_BYTES = 1 * 1024 * 1024

# 静的配信の拡張子 → Content-Type（最小・stdlib mimetypes 相当を明示限定）。
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _nested_error(error_type: str, message: str, generation: int = 0) -> dict[str, Any]:
    """§6.3.4 nested エラーボディ（殻の例外・候補外要求も同形で返す）。"""
    return {
        "ok": False,
        "generation": generation,
        "error": {"type": error_type, "message": message, "violations": []},
    }


def _resolve_static(url_path: str) -> Path | None:
    """URL パスを web/ ルート内の実ファイルへ解決する（パストラバーサル防止）。

    ``/`` は index.html へ。正規化後に web/ ルート外を指す場合・存在しない場合は None。
    """
    rel = url_path.lstrip("/")
    if rel == "":
        rel = "index.html"
    # 正規化（``..`` を解決）した上で web/ ルート内かを厳密判定する。
    candidate = (_WEB_ROOT / rel).resolve()
    try:
        candidate.relative_to(_WEB_ROOT)
    except ValueError:
        # ルート外（``..`` 等で外へ抜けた）→ 拒否。
        return None
    if not candidate.is_file():
        return None
    return candidate


class IndicatorUIRequestHandler(BaseHTTPRequestHandler):
    """/compute・/candles・静的配信を捌くハンドラ（薄殻）。"""

    server_version = "IndicatorUI/0.1"

    # ---- 応答ヘルパ --------------------------------------------------------- #
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # 開発サーバ: 静的 JS/HTML を都度再取得させ、ブラウザの ES モジュール古いキャッシュで
        # 修正が反映されない問題を防ぐ（プロトタイプ前提・キャッシュ無効）。
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    # ---- POST /compute ------------------------------------------------------ #
    def do_POST(self) -> None:  # noqa: N802（stdlib 規定の命名）
        parsed = urlparse(self.path)
        if parsed.path != "/compute":
            self._send_json(404, _nested_error("internal", f"未知のエンドポイント: {parsed.path}"))
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY_BYTES:
            self._send_json(413, _nested_error("validation", "リクエスト本文が大きすぎます。"))
            return

        try:
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                self._send_json(400, _nested_error("validation", "JSON オブジェクトを送信してください。"))
                return
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, _nested_error("validation", f"JSON 解析に失敗しました: {exc}"))
            return

        try:
            status, payload = handle_compute(body)
        except Exception as exc:  # noqa: BLE001（殻の最後の砦・nested で返す）
            self._send_json(500, _nested_error("internal", f"サーバ内部エラー: {exc}"))
            return

        self._send_json(status, payload)

    # ---- GET /candles・静的配信 -------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/candles":
            self._handle_candles(parse_qs(parsed.query))
            return
        self._handle_static(parsed.path)

    def _handle_candles(self, query: dict[str, list[str]]) -> None:
        ref = (query.get("datasetRef") or [None])[0]
        if not dataset.is_known(ref):
            self._send_json(400, _nested_error("validation", f"未知の datasetRef です: {ref!r}"))
            return
        try:
            candles = dataset.load_candles(ref)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, _nested_error("internal", f"candles 取得に失敗しました: {exc}"))
            return
        self._send_json(200, {"ok": True, "candles": candles})

    def _handle_static(self, url_path: str) -> None:
        target = _resolve_static(url_path)
        if target is None:
            self._send_bytes(404, "text/plain; charset=utf-8", b"404 Not Found")
            return
        content_type = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        try:
            body = target.read_bytes()
        except OSError:
            self._send_bytes(404, "text/plain; charset=utf-8", b"404 Not Found")
            return
        self._send_bytes(200, content_type, body)

    # ---- ログ最小化 --------------------------------------------------------- #
    def log_message(self, fmt: str, *args: Any) -> None:
        # 最小ログ（メソッド + パス + ステータス）を stderr へ 1 行。
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """localhost で HTTP サーバを起動する（§7.3 localhost バインドのみ）。

    単一スレッドの ``HTTPServer`` を用いる：fitter="tgp" は rpy2 経由で埋め込み R を呼ぶが、
    R はスレッド非安全で、リクエストごとに別スレッドで処理する ``ThreadingHTTPServer`` だと
    2 回目以降の R 呼び出しが失敗する。全リクエストを同一（メイン）スレッドで直列処理して
    R をスレッド安全に保つ（ローカル単一ユーザー前提のため直列化の影響は無視できる）。
    """
    httpd = HTTPServer((host, port), IndicatorUIRequestHandler)
    url = f"http://{host}:{port}/"
    sys.stdout.write(f"インジケーター管理 UI（B方式）を起動しました: {url}\n")
    sys.stdout.write("  POST /compute  GET /candles?datasetRef=sample  GET /（web/ 静的配信）\n")
    sys.stdout.write("  停止: Ctrl-C\n")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\n停止します。\n")
    finally:
        httpd.server_close()


def _parse_port(argv: list[str]) -> int:
    """引数からポートを取得する（``serve [PORT]`` / ``--port PORT``・既定 8000）。"""
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            return int(argv[i + 1])
        if a.isdigit():
            return int(a)
    return 8000


if __name__ == "__main__":
    serve(port=_parse_port(sys.argv[1:]))
