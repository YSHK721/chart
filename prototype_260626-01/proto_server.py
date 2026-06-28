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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import threading
import pandas as pd

resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))  # price_range_power 等の暴走を catch 可能化
HERE = Path(__file__).resolve().parent          # prototype_260626-01/
WEB = HERE / "web"                              # 配信ルート（本番 web/ のコピー）

# 実バックエンド（indicator_ui api）を読み取り専用 import する。パッケージルートは api/（`from adapter...`）。
_API = HERE.parent / "indigators" / "indicator_ui" / "api"
sys.path.insert(0, str(_API))
from adapter.compute import dataset, IndicatorComputeAdapter            # noqa: E402
from adapter.compute.latest_dispatch import full_compute, latest_compute  # noqa: E402
from marketdata.resample import resample_ohlc, TIMEFRAME_RULES          # noqa: E402（上位足規則・流用）
# 実ティック窓読み込みは contact_scan.tick_window が唯一の実装源（do_intraday から抽出・bit 一致）。
#   TICK_ROOT / OUTLIER_THRESHOLD もそこへ移設し、ここからは import で参照する。
from contact_scan.tick_window import window_ticks, OUTLIER_THRESHOLD    # noqa: E402
ADAPTER = IndicatorComputeAdapter()

# ---- ティック由来データ（ref="jp225_tick"）: 足も足内更新も同一ティック(mid・UTC)由来に一本化 ----
#   prep_tick_rollup.py が生成した M1 原子を読み、上位足は resample_ohlc で生成（既存基盤流用）。
#   日足の高安 = 当日 UTC ティックの最大/最小 = /intraday のティックと一致 ⇒ 書き変わり無し。
TICK_M1_CSV = HERE.parent / "data" / "marketdata" / "jp225_tick_m1.csv"
_tick_m1: dict = {}


# OUTLIER_THRESHOLD は contact_scan.tick_window へ移設済み（上で import）。同一基準（0.3=±30%）。
def _repair_day_outliers(df, threshold: float = OUTLIER_THRESHOLD):
    """日内 close 中央値から OHLC のいずれかが threshold 超で乖離する M1 行を除去する（読み取り時補正）。

    Dukascopy の区間欠損で 1 分足が極端に乖離する（例: 2025-08-26 の ~15100＝当日 ~42600 から
    約 -64%）外れバーのみを安全に分離する。生 CSV は不変＝再生成不要・元データ無改変。
    指数は日中に中央値比 ±30% も動かないため、配信欠損ファントムのみが該当する。
    """
    if len(df) == 0:
        return df
    day = df.index.normalize()                         # 暦日キー（UTC・tz-naive）
    med = df.groupby(day)["close"].transform("median")  # 各行＝その日の close 中央値
    dev = pd.concat([(df[c] / med - 1.0).abs() for c in ("open", "high", "low", "close")],
                    axis=1).max(axis=1)
    mask = (med > 0) & (dev > threshold)
    n = int(mask.sum())
    if n:
        print(f"[tick-clean] M1 外れバー {n} 本を除去（日内中央値±{int(threshold * 100)}%超・例 2025-08-26）")
    return df[~mask]


def _load_tick_m1():
    mt = TICK_M1_CSV.stat().st_mtime
    if _tick_m1.get("mt") != mt:
        df = pd.read_csv(TICK_M1_CSV, parse_dates=["date"]).set_index("date")
        df = _repair_day_outliers(df)                  # 読み取り時に外れバー除去（生CSVは不変）
        _tick_m1.update(mt=mt, df=df)
    return _tick_m1["df"]


def load_tick_candles(tf, limit):
    df = _load_tick_m1()
    rule = None if (tf in (None, "1m")) else TIMEFRAME_RULES.get(tf)
    r = resample_ohlc(df, rule)
    if isinstance(limit, int) and limit > 0:
        r = r.tail(limit)
    secs = r.index.values.astype("datetime64[s]").astype("int64")
    return [{"time": int(secs[i]), "open": float(x.open), "high": float(x.high),
             "low": float(x.low), "close": float(x.close)}
            for i, x in enumerate(r.itertuples(index=False))]

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


def _apply_forming(df, forming):
    """形成中バー（フロントが足内アニメで描いている最新足の暫定 OHLC）を df 末尾へ set/replace する。

    再生の足内更新で MA をローソクに追従させるための差し込み。forming["time"] が df 末尾と同一なら
    その足を暫定 OHLC で置換、新しければ追加する（本番 forming_bar.apply_forming_bar と同じ規則）。
    既存末尾より過去の time（異常）は触らない。列名は大小無関係で照合する。
    """
    if not isinstance(forming, dict) or len(df) == 0:
        return df
    try:
        t = pd.Timestamp(int(forming["time"]), unit="s")
    except (KeyError, TypeError, ValueError):
        return df
    if t < df.index[-1]:                                   # 形成中が既存末尾より過去 → 触らない（防御）
        return df
    out = df.copy()
    lower = {str(c).lower(): c for c in out.columns}
    for key in ("open", "high", "low", "close", "volume"):
        col = lower.get(key)
        if col is not None and key in forming:
            out.loc[t, col] = float(forming[key])
    return out.sort_index()

# ThreadingHTTPServer 化に伴う 2 つのスレッド由来リスクを 1 本のロックで封じる:
#   (1) R(rpy2) は単一インタプリタでスレッド非安全 → /compute の R 呼び出しを直列化。
#   (2) ティック candles の resample（約4M行→152MiB）/ intraday が並行多重化すると
#       RLIMIT_AS(3GB) を突破して MemoryError(=チャート 400・ローディング滞留) になる
#       → 重いデータ処理も同ロックでピークメモリを 1 処理分に抑える。
# 軽量な静的配信は本ロックを取らないため、重い処理が走っても UI シェルは即時表示できる。
_HEAVY_LOCK = threading.Lock()

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
        # 足内更新: 形成中バー（フロントの暫定 OHLC）を末尾へ差し込んでから latest 計算する
        #   ＝MA の末尾点がローソクの足内変化に追従する。forming 無し（バー確定再計算）は不変。
        df = _apply_forming(df, body.get("forming"))
        series = latest_compute(ADAPTER, indicator, variant, df, params)
    else:
        series = full_compute(ADAPTER, indicator, variant, df, params)
    _log_compute(body, df, series)            # A方式: 入力＋出力を .log に記録
    return series

def _downsample_keep_extremes(mid, n):
    """mid 列を最大 n 点へ間引く。最高/最安/先頭/末尾は必ず残す（日足の高安＝集計の最大/最小と
    一致させ、足内更新後の書き変わりを防ぐ）。返り値は時系列順・小数3桁丸め。"""
    if len(mid) <= n:
        return mid
    i_max = max(range(len(mid)), key=lambda i: mid[i])
    i_min = min(range(len(mid)), key=lambda i: mid[i])
    keep = {0, len(mid) - 1, i_max, i_min}
    stride = len(mid) / n
    for k in range(n):
        keep.add(int(k * stride))
    return [mid[i] for i in sorted(keep)]      # 丸めない（日足高安＝集計の最大/最小と bit 一致）


def _cap_m1_rows(rows, n):
    """m1 OHLC 行列を最大 n 行へ間引く（上位足 1W/1M の m1 が数千行＝ペイロード肥大を抑制）。
    先頭/末尾＋窓内の最高高値・最安安値の行は必ず残す（足の高安が消えない）。1D 以下は n 以内で無変更。"""
    if len(rows) <= n:
        return rows
    i_hi = max(range(len(rows)), key=lambda i: rows[i][1])  # high 最大
    i_lo = min(range(len(rows)), key=lambda i: rows[i][2])  # low 最小
    keep = {0, len(rows) - 1, i_hi, i_lo}
    stride = len(rows) / n
    for k in range(n):
        keep.add(int(k * stride))
    return [rows[i] for i in sorted(keep)]


def do_intraday(ref, start, end, mode="real_ticks"):
    """足 1 本の区間 [start,end) の足内データを返す（最新足更新用）。

    m1    : 区間の 1 分足 OHLC（1分OHLC/全ティック合成の素）。上位足のペイロードは ~1500 行に cap。
    ticks : 区間の実ティック mid 列（real_ticks の素）。**real_ticks 時のみ全ティック（cap 廃止）を返す**。
            実ティックの目的＝取引成立の接点検証＝間引かない（日足で確定の絶対仕様・時間足で不変）。
            他モードは ticks 読込をスキップして軽量に保つ（重くしたくなければ実ティックを選ばない）。
    epoch は candle.time と同一エンコード（index.asi8//1e9 == pd.Timestamp(i).timestamp()）。
    """
    out = {"ok": True, "m1": [], "ticks": []}
    try:
        df = _load_tick_m1() if ref == "jp225_tick" else dataset.load_dataframe(ref, "1m")
        # index は datetime64[us]（tz-naive・UTC扱い）。単位非依存に秒へ変換（candle.time と一致）。
        secs = df.index.values.astype("datetime64[s]").astype("int64")
        sub = df[(secs >= start) & (secs < end)]
        rows = [[float(r.open), float(r.high), float(r.low), float(r.close)]
                for r in sub.itertuples(index=False)]
        out["m1"] = _cap_m1_rows(rows, 1500)   # 1D(≤1440)は無変更／1W・1M(数千〜数万)はペイロード抑制
    except Exception as e:
        out["m1_error"] = str(e)[:120]
    if mode != "real_ticks":
        return out                       # 他モードは m1 のみで足りる＝tick 読込をスキップ（軽量維持）
    try:
        # 足の期間 [start,end) の実ティック mid 列（接点検証＝全件・cap 無し）。
        #   ロジックは contact_scan.tick_window.window_ticks に移設（[start,end) 跨ぎ全 UTC 日 parquet
        #   走査＋timestamp 窓フィルタ＋mid=(bid+ask)/2＋窓内 mid 中央値±threshold 外れ値除去）。
        #   返り (sec, mid) のうち mid のみ取り出し従来同型（float list・順序・値とも bit 一致）。
        out["ticks"] = [m for _, m in window_ticks(start, end)]
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
            if ref == "jp225_tick":
                try:
                    with _HEAVY_LOCK:                 # 巨大 resample を直列化（並行多重で OOM 防止）
                        candles = load_tick_candles(tf, lim)
                    return self._json(200, {"ok": True, "candles": candles})
                except Exception as e:
                    return self._json(400, {"error": {"type": "internal", "message": str(e)[:200]}})
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
            mode = (q.get("mode") or ["real_ticks"])[0]   # 実ティックのみ全ティック返却（他モードは軽量＝tick読込スキップ）
            if ref != "jp225_tick" and not dataset.is_known(ref):
                return self._json(400, {"error": {"type": "validation", "message": f"unknown {ref}"}})
            try:
                with _HEAVY_LOCK:                     # ティック読み込み/集計を直列化（OOM 防止）
                    payload = do_intraday(ref, start, end, mode)
                return self._json(200, payload)
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
            with _HEAVY_LOCK:                    # R(rpy2)非スレッド安全＋メモリのため直列化
                series = do_compute(body)
        except MemoryError:
            return self._json(400, {"error": {"type": "internal", "message": "memory limit"}})
        except Exception as e:
            return self._json(400, {"error": {"type": "validation", "message": f"{type(e).__name__}: {str(e)[:200]}"}})
        self._json(200, {"ok": True, "generation": gen, "series": series})

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8796
    print(f"prototype_260626-01（本番フロント＋再生）: http://127.0.0.1:{port}/  (Ctrl-C 停止)")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
