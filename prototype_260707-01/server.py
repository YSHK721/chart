#!/usr/bin/env python3
"""prototype_260707-01 — 5秒遅延ティック再生の試作サーバ（使い捨て）。

freeserv 増分カーソル API（dukascopy_python._fetch・公式ライブウィジェットと同じ endpoint）を
5 秒周期・直列 1 接続でポーリングし、直近 tick をメモリバッファへ貯める。フロント（index.html）は
/ticks?since= で増分取得し、固定遅延（既定 7 秒）で元の時間間隔どおり再生する。

行儀（依頼者確認済みの安全装置）:
  - カーソル維持＝重複ダウンロードなし（1 回のポーリングは直近数秒分・数 KB）。
  - エラー時は指数バックオフ（5→10→20→40→最大 60 秒）。連続 8 失敗で 10 分停止
    （サーキットブレーカ）。並列 fetch なし（直列 1 接続）。

既存資産は読み取り専用（marketdata の JP225 定数のみ借用）。data/ へは一切書かない。
使い方: PYTHONPATH=<repo> python3 server.py [PORT(既定8281)] [--interval 5]
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import dukascopy_python  # noqa: E402
from dukascopy_python import _fetch  # noqa: E402  試作: 増分カーソルの生 API を直接使う
from marketdata import JP225  # noqa: E402

WEB_DIR = Path(__file__).parent / "web"
POLL_INTERVAL = 5.0
BUFFER_KEEP_MS = 30 * 60 * 1000  # バッファ保持 30 分

# 共有状態（バッファ・メトリクス）。lock で守る。
_lock = threading.Lock()
_ticks: list[tuple[int, float]] = []  # (unix_ms, mid) 昇順
_polls: list[dict] = []  # 直近ポーリングのメトリクス
_state = {"status": "starting", "errors_in_row": 0, "paused_until": 0.0}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _poll_loop(interval: float) -> None:
    """増分カーソルポーリング（直列 1 接続・バックオフ・サーキットブレーカ）。"""
    # 初期カーソル: 30 分前から（初回だけ数リクエストで catch-up し、以降は増分）。
    cursor = _now_ms() - BUFFER_KEEP_MS
    backoff = interval
    while True:
        if time.time() < _state["paused_until"]:
            time.sleep(1.0)
            continue
        t0 = time.time()
        try:
            rows = _fetch(
                instrument=JP225,
                interval=dukascopy_python.INTERVAL_TICK,
                offer_side=dukascopy_python.OFFER_SIDE_BID,
                last_update=cursor,
                limit=30_000,
            )
            dur = time.time() - t0
            new = [(int(r[0]), (float(r[1]) + float(r[2])) / 2.0) for r in rows if int(r[0]) > cursor]
            with _lock:
                if new:
                    _ticks.extend(new)
                    cursor = new[-1][0]
                    cut = _now_ms() - BUFFER_KEEP_MS
                    while _ticks and _ticks[0][0] < cut:
                        _ticks.pop(0)
                _polls.append({
                    "t": _now_ms(),
                    "dur_ms": int(dur * 1000),
                    "n_new": len(new),
                    "newest_ms": cursor,
                    "lag_ms": _now_ms() - cursor,
                })
                del _polls[:-200]
                _state["status"] = "ok"
                _state["errors_in_row"] = 0
            backoff = interval
        except Exception as exc:  # noqa: BLE001 — 試作: 一過性障害はバックオフして継続
            with _lock:
                _state["errors_in_row"] += 1
                _state["status"] = f"error: {type(exc).__name__}: {exc}"
                if _state["errors_in_row"] >= 8:  # サーキットブレーカ: 10 分停止
                    _state["paused_until"] = time.time() + 600
                    _state["errors_in_row"] = 0
                    _state["status"] += " (circuit-breaker: 10min pause)"
            backoff = min(backoff * 2, 60.0)
        time.sleep(max(0.0, backoff - (time.time() - t0)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静かに
        pass

    def _json(self, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/ticks":
            since = int(parse_qs(u.query).get("since", ["0"])[0])
            with _lock:
                out = [t for t in _ticks if t[0] > since]
            self._json({"ticks": out, "server_now_ms": _now_ms()})
            return
        if u.path == "/stats":
            with _lock:
                polls = list(_polls[-60:])
                n = len(_ticks)
                newest = _ticks[-1][0] if _ticks else None
                st = dict(_state)
            self._json({"state": st, "buffer_ticks": n, "newest_ms": newest, "polls": polls})
            return
        # 静的ファイル
        name = "index.html" if u.path == "/" else u.path.lstrip("/")
        f = (WEB_DIR / name).resolve()
        if not str(f).startswith(str(WEB_DIR.resolve())) or not f.is_file():
            self.send_error(404)
            return
        body = f.read_bytes()
        ctype = "text/html; charset=utf-8" if f.suffix == ".html" else "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    port = 8281
    interval = POLL_INTERVAL
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--interval" and i + 1 < len(args):
            interval = float(args[i + 1])
        elif a.isdigit():
            port = int(a)
    threading.Thread(target=_poll_loop, args=(interval,), daemon=True).start()
    print(f"prototype 260707-01: http://127.0.0.1:{port}/ (poll={interval}s)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
