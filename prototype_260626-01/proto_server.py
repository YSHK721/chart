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
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import pandas as pd

resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))  # price_range_power 等の暴走を catch 可能化
HERE = Path(__file__).resolve().parent          # prototype_260626-01/
WEB = HERE / "web"                              # 配信ルート（本番 web/ のコピー）
TICK_ROOT = HERE.parent / "data" / "marketdata" / "ticks"  # 実ティック parquet（read-only）

# 実バックエンド（indicator_ui api）を読み取り専用 import する。パッケージルートは api/（`from adapter...`）。
_API = HERE.parent / "indigators" / "indicator_ui" / "api"
sys.path.insert(0, str(_API))
from adapter.compute import dataset, IndicatorComputeAdapter            # noqa: E402
from adapter.compute.latest_dispatch import full_compute, latest_compute  # noqa: E402
ADAPTER = IndicatorComputeAdapter()

# 計算ログ（A方式）: 各 /compute の入力(params・データ窓)＋出力(全系列)を JSON Lines で追記する。
#   計算式そのもの（演算ステップ）はインジのソースに定義された静的なものなので、ここでは
#   「どの入力・パラメータで計算し、何が出たか」を一意に再現できる形で記録する。
#   ※全足OHLC＋全系列を毎フレーム記録するため肥大しやすい。不要時は env LOG_COMPUTE=0 で無効化、
#     ファイルは compute.log（gitignore 済）。
LOG_PATH = HERE / "compute.log"
import os  # noqa: E402
LOG_ENABLED = os.environ.get("LOG_COMPUTE", "1") != "0"

def _log_compute(body, df, series):
    if not LOG_ENABLED:
        return
    try:
        bars = [[int(pd.Timestamp(i).timestamp()), float(r.open), float(r.high), float(r.low), float(r.close)]
                for i, r in df.iterrows()]
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "request": {k: body.get(k) for k in
                        ("indicatorId", "variant", "params", "datasetRef", "timeframe", "limit", "mode", "untilTime")},
            "input": {"n": len(bars),
                      "first_time": bars[0][0] if bars else None,
                      "last_time": bars[-1][0] if bars else None,
                      "bars": bars},          # データ窓全て: [time, open, high, low, close]
            "output": {"series": series},     # 出力全系列: name / kind / data|lines
        }
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # ログ失敗は計算をブロックしない

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
        series = latest_compute(ADAPTER, indicator, variant, df, params)
    else:
        series = full_compute(ADAPTER, indicator, variant, df, params)
    _log_compute(body, df, series)            # A方式: 入力＋出力を .log に記録
    return series

def do_intraday(ref, start, end):
    """日足 1 本の区間 [start,end) の足内データを返す（最新足の 5 モード更新用）。

    m1    : 当該日の 1 分足 OHLC 列（dataset を区間スライス）。1分OHLC/全ティック合成の素。
    ticks : 当該 UTC 日の実ティック parquet の mid 列（~800 点へ間引き）。real_ticks の素。
    epoch は candle.time と同一エンコード（index.asi8//1e9 == pd.Timestamp(i).timestamp()）。
    """
    out = {"ok": True, "m1": [], "ticks": []}
    try:
        df = dataset.load_dataframe(ref, "1m")
        # index は datetime64[us]（tz-naive・UTC扱い）。単位非依存に秒へ変換（candle.time と一致）。
        secs = df.index.values.astype("datetime64[s]").astype("int64")
        sub = df[(secs >= start) & (secs < end)]
        out["m1"] = [[float(r.open), float(r.high), float(r.low), float(r.close)]
                     for r in sub.itertuples(index=False)]
    except Exception as e:
        out["m1_error"] = str(e)[:120]
    try:
        d = datetime.fromtimestamp(start, tz=timezone.utc)
        p = TICK_ROOT / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}" / "JP225_ticks.parquet"
        if p.is_file():
            tdf = pd.read_parquet(p, columns=["bidPrice", "askPrice"])
            mid = ((tdf["bidPrice"] + tdf["askPrice"]) / 2.0).tolist()
            step = max(1, len(mid) // 800)
            out["ticks"] = [round(mid[j], 3) for j in range(0, len(mid), step)]
    except Exception as e:
        out["ticks_error"] = str(e)[:120]
    return out


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
        if u.path == "/intraday":
            ref = (q.get("datasetRef") or ["jp225_m1"])[0]
            try:
                start = int(q["start"][0]); end = int(q["end"][0])
            except Exception:
                return self._json(400, {"error": {"type": "validation", "message": "start/end required"}})
            if not dataset.is_known(ref):
                return self._json(400, {"error": {"type": "validation", "message": f"unknown {ref}"}})
            try:
                return self._json(200, do_intraday(ref, start, end))
            except Exception as e:
                return self._json(400, {"error": {"type": "internal", "message": str(e)[:200]}})
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
