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
from adapter.compute import forming_bar as forming_bar_mod  # noqa: E402
from adapter.controller.compute_controller import handle_compute  # noqa: E402
from adapter.controller.market_profile_controller import handle_market_profile  # noqa: E402

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
        if parsed.path == "/forming_bar":
            self._handle_forming_bar(parse_qs(parsed.query))
            return
        if parsed.path == "/market_profile":
            self._handle_market_profile(parse_qs(parsed.query))
            return
        self._handle_static(parsed.path)

    def _handle_candles(self, query: dict[str, list[str]]) -> None:
        ref = (query.get("datasetRef") or [None])[0]
        if not dataset.is_known(ref):
            self._send_json(400, _nested_error("validation", f"未知の datasetRef です: {ref!r}"))
            return
        # timeframe（時間足）— 省略は原子（再集計なし・後方互換）。未知コードは 400。
        timeframe = (query.get("timeframe") or [None])[0]
        if timeframe is not None and not dataset.is_known_timeframe(timeframe):
            self._send_json(400, _nested_error("validation", f"未知の timeframe です: {timeframe!r}"))
            return
        # limit（直近 N 本）— 数字のみ採用。1 分足原子の全件直接配信を避ける表示範囲制限。
        limit_raw = (query.get("limit") or [None])[0]
        limit = int(limit_raw) if (limit_raw and limit_raw.isdigit()) else None
        try:
            candles = dataset.load_candles(ref, timeframe, limit)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, _nested_error("internal", f"candles 取得に失敗しました: {exc}"))
            return
        self._send_json(200, {"ok": True, "candles": candles})

    def _handle_forming_bar(self, query: dict[str, list[str]]) -> None:
        """GET /forming_bar — 選択 tf の現在「形成中バー」を返す（ライブ足内更新用・読取のみ）。

        ``{ok: True, bar: {time,open,high,low,close,volume} | null}``。対象外 ref/tf・期間内
        ティック無しは ``bar=null``（エラーではなく「更新なし」）。``now``（UNIX 秒）省略時は実 UTC 現在。
        """
        ref = (query.get("datasetRef") or [None])[0]
        if not dataset.is_known(ref):
            self._send_json(400, _nested_error("validation", f"未知の datasetRef です: {ref!r}"))
            return
        timeframe = (query.get("timeframe") or [None])[0]
        if timeframe is not None and not dataset.is_known_timeframe(timeframe):
            self._send_json(400, _nested_error("validation", f"未知の timeframe です: {timeframe!r}"))
            return
        now_raw = (query.get("now") or [None])[0]
        now_override = int(now_raw) if (now_raw and now_raw.lstrip("-").isdigit()) else None
        # now は forming_bar.resolve_now_unix に一元化（query now 優先→デモ時計→実 now）。
        now_unix = forming_bar_mod.resolve_now_unix(now_override)
        try:
            bar = forming_bar_mod.forming_bar(ref, timeframe, now_unix)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, _nested_error("internal", f"forming_bar 取得に失敗しました: {exc}"))
            return
        self._send_json(200, {"ok": True, "bar": bar})

    def _handle_market_profile(self, query: dict[str, list[str]]) -> None:
        """GET /market_profile — 足ベース TPO マーケットプロファイルを返す（読取のみ）。

        検証（未知 ref/tf は 400）・計算は純ロジック ``handle_market_profile`` に委譲し、本メソッドは
        クエリ取り出しと (status, payload) の JSON 応答のみを担う（``_handle_forming_bar`` と同型の薄殻）。
        応答は ``{ok:true, profile:{...}}``（bins/poc/va_low/va_high/price_min/price_max/tpo_units/n_bins）。
        """
        ref = (query.get("datasetRef") or [None])[0]
        timeframe = (query.get("timeframe") or [None])[0]
        limit = (query.get("limit") or [None])[0]
        bins = (query.get("bins") or [None])[0]
        va = (query.get("va") or [None])[0]
        src = (query.get("src") or [None])[0]
        barw = (query.get("barw") or [None])[0]
        to = (query.get("to") or [None])[0]  # リプレイ時間カーソル（UNIX 秒・省略時=全期間＝現行挙動）。
        frm = (query.get("from") or [None])[0]  # ローリング窓の下限 time（UNIX 秒・省略時=全期間）。増分2 A。
        today = (query.get("today") or [None])[0]  # スナップショット当日強調（'1' で today[]/today_max 付加）。増分2 C。
        try:
            status, payload = handle_market_profile(
                ref, timeframe, limit, bins, va, src, barw, to, **{"from": frm, "today": today}
            )
        except Exception as exc:  # noqa: BLE001（殻の最後の砦・nested で返す）
            self._send_json(500, _nested_error("internal", f"market_profile 取得に失敗しました: {exc}"))
            return
        self._send_json(status, payload)

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
