"""serve_replay — 因果リビール再生バックエンドの HTTP フレームワーク層（proto_server 忠実）。

エンドポイント（proto と同一プロトコル）:
    GET  /candles?datasetRef=&timeframe=&limit=            → {ok, candles}
    POST /compute {indicatorId,variant,params,datasetRef,timeframe,limit,generation,mode,
                   untilTime,forming}                       → {ok, generation, series}
    GET  /intraday?datasetRef=&start=&end=&mode=            → {ok, m1, ticks[, *_error]}
    GET  /（静的）                                            → web_dir 配信（任意・no-store）

CLEAN_ARCH §6: HTTP・スレッド・静的配信という偶有的技術を最外層へ隔離する。R(rpy2) 非スレッド安全
＋巨大 resample の OOM 回避のため重い処理を 1 本の ``_HEAVY_LOCK`` で直列化する（proto と同一方針・
出力は不変）。例外翻訳（MemoryError→internal / それ以外→validation）も proto do_POST に一致。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from simulator.replay_ui.usecase.causal_compute import (
    CausalComputeRequest,
    causal_compute,
)
from simulator.replay_ui.usecase.intrabar_window import (
    IntrabarWindowRequest,
    intrabar_window,
)
from simulator.replay_ui.usecase.reveal_candles import (
    RevealCandlesRequest,
    reveal_candles,
)


class ReplayApp:
    """UC 結線を保持し、HTTP ハンドラから呼ばれるアプリケーション面（framework 層）。

    ``is_known_ref``: /intraday の事前 ref 検証（proto do_GET /intraday 忠実）。None のとき検証省略。
    ``heavy_lock``: 重い処理の直列化ロック（R 非安全＋OOM 回避）。既定は新規 Lock。
    """

    def __init__(
        self,
        *,
        candle_port: Any,
        compute_port: Any,
        window_port: Any,
        is_known_ref: "Optional[Callable[[str], bool]]" = None,
        web_dir: Any = None,
        heavy_lock: "Optional[threading.Lock]" = None,
    ) -> None:
        self._candle_port = candle_port
        self._compute_port = compute_port
        self._window_port = window_port
        self._is_known_ref = is_known_ref
        self.web_dir = Path(web_dir).resolve() if web_dir else None
        self._lock = heavy_lock if heavy_lock is not None else threading.Lock()

    def candles(self, ref: str, tf: "str | None", limit: "int | None") -> "list[dict]":
        req = RevealCandlesRequest(ref=ref, timeframe=tf, limit=limit)
        with self._lock:  # 巨大 resample を直列化（並行多重で OOM 防止）
            return reveal_candles(request=req, candle_port=self._candle_port)

    def compute(self, body: dict) -> "list[dict]":
        req = CausalComputeRequest(
            indicator=body.get("indicatorId"),
            variant=body.get("variant", "default"),
            ref=body.get("datasetRef"),
            timeframe=body.get("timeframe"),
            limit=body.get("limit"),
            until_time=body.get("untilTime"),
            mode=body.get("mode"),
            forming=body.get("forming"),
            params=dict(body.get("params") or {}),
        )
        with self._lock:  # R(rpy2) 非スレッド安全＋メモリのため直列化
            return causal_compute(request=req, compute_port=self._compute_port)

    def intraday(self, ref: str, start: int, end: int, mode: str) -> dict:
        # proto do_GET /intraday: 非 tick の未知 ref は事前に validation 拒否する。
        if self._is_known_ref is not None and ref != "jp225_tick" and not self._is_known_ref(ref):
            raise ValueError(f"unknown {ref}")
        req = IntrabarWindowRequest(ref=ref, start=start, end=end, mode=mode)
        with self._lock:  # ティック読込/集計を直列化（OOM 防止）
            res = intrabar_window(request=req, window_port=self._window_port)
        payload: dict = {"ok": res.ok, "m1": res.m1, "ticks": res.ticks}
        if res.m1_error is not None:
            payload["m1_error"] = res.m1_error
        if res.ticks_error is not None:
            payload["ticks_error"] = res.ticks_error
        return payload


def make_handler(app: ReplayApp):
    """``app`` を束ねた BaseHTTPRequestHandler サブクラスを返す（proto H 忠実）。"""

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, obj: Any) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: D401 — アクセスログ抑制（proto と同一）
            pass

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/candles":
                ref = (q.get("datasetRef") or ["jp225_m1"])[0]
                tf = (q.get("timeframe") or [None])[0]
                lim = int(q["limit"][0]) if "limit" in q else None
                try:
                    candles = app.candles(ref, tf, lim)
                    return self._json(200, {"ok": True, "candles": candles})
                except ValueError as e:
                    return self._json(400, {"error": {"type": "validation", "message": str(e)[:200]}})
                except Exception as e:  # noqa: BLE001
                    return self._json(400, {"error": {"type": "internal", "message": str(e)[:200]}})
            if u.path == "/intraday":
                ref = (q.get("datasetRef") or ["jp225_m1"])[0]
                try:
                    start = int(q["start"][0])
                    end = int(q["end"][0])
                except Exception:  # noqa: BLE001
                    return self._json(400, {"error": {"type": "validation", "message": "start/end required"}})
                mode = (q.get("mode") or ["real_ticks"])[0]
                try:
                    payload = app.intraday(ref, start, end, mode)
                    return self._json(200, payload)
                except ValueError as e:
                    return self._json(400, {"error": {"type": "validation", "message": str(e)[:200]}})
                except Exception as e:  # noqa: BLE001
                    return self._json(400, {"error": {"type": "internal", "message": str(e)[:200]}})
            return self._serve_static(u.path)

        def _serve_static(self, path: str):
            if app.web_dir is None:
                self.send_response(404)
                self.end_headers()
                return
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            fp = (app.web_dir / rel).resolve()
            if not str(fp).startswith(str(app.web_dir)) or not fp.is_file():
                self.send_response(404)
                self.end_headers()
                return
            ct = {
                "html": "text/html", "js": "application/javascript",
                "mjs": "application/javascript", "css": "text/css",
                "json": "application/json",
            }.get(fp.suffix.lstrip("."), "text/plain")
            body = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            if urlparse(self.path).path != "/compute":
                self.send_response(404)
                self.end_headers()
                return
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            gen = body.get("generation", 0)
            try:
                series = app.compute(body)
            except MemoryError:
                return self._json(400, {"error": {"type": "internal", "message": "memory limit"}})
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"error": {"type": "validation", "message": f"{type(e).__name__}: {str(e)[:200]}"}})
            self._json(200, {"ok": True, "generation": gen, "series": series})

    return Handler


def make_server(app: ReplayApp, host: str = "127.0.0.1", port: "int | None" = None) -> ThreadingHTTPServer:
    """サーバを生成して返す（起動はしない）。``port=None`` は空きポート（8796 衝突回避）。"""
    server = ThreadingHTTPServer((host, port or 0), make_handler(app))
    return server


def serve(app: ReplayApp, host: str = "127.0.0.1", port: "int | None" = None) -> None:
    """サーバを起動して待ち受ける（ブロッキング）。"""
    server = make_server(app, host, port)
    actual = server.server_address[1]
    print(f"replay backend: http://{host}:{actual}/  (Ctrl-C 停止)")
    server.serve_forever()
