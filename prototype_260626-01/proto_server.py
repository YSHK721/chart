"""prototype_260626-01 サーバ（本番 indicator_ui プロトコル準拠＋untilTime）。

本番フロント（web/ は本番 web/ のコピー）をそのまま配信し、データ駆動だけ「ライブ→再生」に
差し替えるためのバックエンド。本番と同じ /candles・/compute を話し、**untilTime（再生のその時点）**
だけを追加で受ける。計算は indicator_ui の実アダプタ full_compute/latest_compute（偽装なし）。

  GET  /candles?datasetRef=&timeframe=&limit=   → {ok, candles}
  POST /compute {indicatorId,variant,params,datasetRef,timeframe,limit,generation,mode,untilTime}
        → df を timeframe で取得 → untilTime まで切断 → tail(limit) → 計算 → {ok,generation,series}
  GET  /（静的）                                  → web/ 配信（本番フロント）

既存データ・indicator_ui コードは読み取り専用。R スレッド非安全のため単一スレッド。
使い方: python3 proto_server.py [PORT]
"""
from __future__ import annotations
import sys, json, resource
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import pandas as pd

resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))  # price_range_power 等の暴走を catch 可能化
HERE = Path(__file__).resolve().parent          # prototype_260626-01/
WEB = HERE / "web"                              # 配信ルート（本番 web/ のコピー）

# 実バックエンド（indicator_ui api）を読み取り専用 import する。パッケージルートは api/（`from adapter...`）。
_API = HERE.parent / "indigators" / "indicator_ui" / "api"
sys.path.insert(0, str(_API))
from adapter.compute import dataset, IndicatorComputeAdapter            # noqa: E402
from adapter.compute.latest_dispatch import full_compute, latest_compute  # noqa: E402
ADAPTER = IndicatorComputeAdapter()

def _truncate(df, until):
    if until is None:
        return df
    return df[[int(pd.Timestamp(i).timestamp()) <= until for i in df.index]]

def do_compute(body):
    indicator = body.get("indicatorId")
    variant = body.get("variant", "default")
    ref = body.get("datasetRef")
    tf = body.get("timeframe")
    if not dataset.is_known(ref):
        raise ValueError(f"unknown datasetRef {ref!r}")
    if tf is not None and not dataset.is_known_timeframe(tf):
        raise ValueError(f"unknown timeframe {tf!r}")
    df = dataset.load_dataframe(ref, tf)
    df = _truncate(df, body.get("untilTime"))            # ← 再生のその時点まで（ライブ同一）
    limit = body.get("limit")
    if isinstance(limit, int) and limit > 0:
        df = df.tail(limit)
    params = dict(body.get("params") or {})
    if len(df) == 0:
        return []
    if body.get("mode") == "latest":
        return latest_compute(ADAPTER, indicator, variant, df, params)
    return full_compute(ADAPTER, indicator, variant, df, params)

class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/candles":
            ref = (q.get("datasetRef") or ["jp225_m1"])[0]
            tf = (q.get("timeframe") or [None])[0]
            lim = int(q["limit"][0]) if "limit" in q else None
            if not dataset.is_known(ref): return self._json(400, {"error": {"type": "validation", "message": f"unknown {ref}"}})
            try:
                cs = dataset.load_candles(ref, tf, lim)
            except Exception as e:
                return self._json(400, {"error": {"type": "internal", "message": str(e)[:200]}})
            return self._json(200, {"ok": True, "candles": cs})
        # 静的配信（web/ 配下のみ・パストラバーサル防止）。
        rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        fp = (WEB / rel).resolve()
        if not str(fp).startswith(str(WEB)) or not fp.is_file():
            self.send_response(404)
            self.end_headers()
            return
        ct = {"html": "text/html", "js": "application/javascript", "mjs": "application/javascript",
              "css": "text/css", "json": "application/json"}.get(fp.suffix.lstrip("."), "text/plain")
        body = fp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        if urlparse(self.path).path != "/compute":
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        gen = body.get("generation", 0)
        try:
            series = do_compute(body)
        except MemoryError:
            return self._json(400, {"error": {"type": "internal", "message": "memory limit"}})
        except Exception as e:
            return self._json(400, {"error": {"type": "validation", "message": f"{type(e).__name__}: {str(e)[:200]}"}})
        self._json(200, {"ok": True, "generation": gen, "series": series})

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8796
    print(f"prototype_260626-01（本番フロント＋再生）: http://127.0.0.1:{port}/  (Ctrl-C 停止)")
    HTTPServer(("127.0.0.1", port), H).serve_forever()
