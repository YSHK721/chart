#!/usr/bin/env python3
"""Market Profile 試作サーバ  prototype_260630-01

起動: python3 prototype_260630-01/mp_server.py [PORT]   (既定 8810)
配信: web/ 静的ファイル
API :
  GET /candles?tf=1D&n=120
      -> {"candles":[{"time","open","high","low","close"}, ...]}
  GET /profile?tf=1D&n=120&bins=60&src=1D&va=0.70&sessions=0
      -> mp_core.compute_profile(...) の JSON（POC/VA/各ビンTPO/(任意)per-session）
         窓は表示足 tf の直近 n 本の時間範囲。src=1D/4h/m1。
読み取り専用。
"""
import os
import sys
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
sys.path.insert(0, HERE)
import mp_core  # noqa: E402

CT = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
      ".css": "text/css", ".json": "application/json"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        ext = os.path.splitext(path)[1]
        try:
            with open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self._json(404, {"error": "not found"}); return
        self.send_response(200)
        self.send_header("Content-Type", CT.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        g = lambda k, d: q.get(k, [d])[0]
        try:
            if u.path == "/candles":
                tf = g("tf", "1D"); n = int(g("n", "120"))
                self._json(200, {"candles": mp_core.load_candles(tf, n)})
            elif u.path == "/profile":
                tf = g("tf", "1D"); n = int(g("n", "120"))
                bins = int(g("bins", "60")); src = g("src", "1D")
                va = float(g("va", "0.70")); sess = g("sessions", "0") == "1"
                today = g("today", "0") == "1"
                disp = mp_core.load_candles(tf, n)
                # 任意の期間指定（マウスドラッグ範囲選択）。from/to(UNIX秒)があればその区間に限定。
                fr = g("from", ""); to = g("to", "")
                if fr and to:
                    f0, f1 = int(fr), int(to)
                    if f1 < f0:
                        f0, f1 = f1, f0
                    sub = [b for b in disp if f0 <= b["time"] <= f1]
                    if len(sub) >= 1:            # 1本窓(左端0時点=from==to)も許可
                        disp = sub
                t0, t1 = disp[0]["time"], disp[-1]["time"]
                pmin = min(b["low"] for b in disp)
                pmax = max(b["high"] for b in disp)
                if pmax <= pmin:                 # 1本窓で low==high のゼロ割回避
                    pmax = pmin + 1.0
                # バーのレンジ(1バーの価格幅 pt)を直接指定 → 本数を算出（bins より優先）
                barw = float(g("barw", "0"))
                if barw > 0 and pmax > pmin:
                    bins = max(1, min(2000, int(round((pmax - pmin) / barw))))
                bar_sec = {"1D": 86400, "4h": 14400}.get(tf, 86400)  # 最終足の終端算出用
                prof = mp_core.compute_profile(t0, t1, pmin, pmax, bins, src=src,
                                               va_pct=va, want_sessions=sess, want_today=today,
                                               bar_sec=bar_sec)
                prof["bar_width"] = round((pmax - pmin) / bins, 2)
                prof["from"] = t0; prof["to"] = t1
                prof["last_close"] = disp[-1]["close"]
                self._json(200, prof)
            elif u.path in ("/", ""):
                self._file(os.path.join(WEB, "index.html"))
            else:
                self._file(os.path.join(WEB, u.path.lstrip("/")))
        except Exception as e:
            self._json(500, {"error": str(e)})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8810
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"Market Profile server: http://localhost:{port}  (web={WEB})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
