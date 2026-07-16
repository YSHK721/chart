"""serve_replay — 因果リビール再生バックエンドの HTTP フレームワーク層（proto_server 忠実）。

エンドポイント（proto と同一プロトコル）:
    GET  /candles?datasetRef=&timeframe=&limit=            → {ok, candles}
    POST /compute {indicatorId,variant,params,datasetRef,timeframe,limit,generation,mode,
                   untilTime,forming}                       → {ok, generation, series}
    GET  /intraday?datasetRef=&start=&end=&mode=            → {ok, m1, ticks[, *_error]}
    GET  /（静的）                                            → web_dir 配信（任意・no-store）

CLEAN_ARCH §6: HTTP・スレッド・静的配信という偶有的技術を最外層へ隔離する。R(rpy2) 非スレッド安全
＋巨大 resample の OOM 回避のため重い処理を 1 本の ``_HEAVY_LOCK`` で直列化する（proto と同一方針・
出力は不変）。エラー応答は正典契約 api_shared.http_contract（ERROR_STATUS・nested_error）に従う
（ISSUE-091 A2: 旧 proto 由来の独自形 {error:{type,message}}・internal→400 という契約分岐を是正。
例外翻訳は ValueError→validation / MemoryError・それ以外→internal）。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

# 正典エラー契約（ISSUE-091 A2 / ISSUE-094 🔵-11）: status 翻訳・nested ボディとも中立共有
#   パッケージ api_shared.http_contract の単一定義を直参照する。
from api_shared.http_contract import nested_error

from simulator.replay_ui.usecase.causal_compute import (
    CausalComputeRequest,
    causal_compute,
)
from simulator.replay_ui.usecase.intrabar_window import (
    IntrabarWindowRequest,
    intrabar_window,
)
from simulator.replay_ui.usecase.market_profile import (
    MarketProfileRequest,
    market_profile,
)
from simulator.replay_ui.usecase.market_profile_forming import (
    MarketProfileFormingRequest,
    market_profile_forming,
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
        shared_js_root: Any = None,
        heavy_lock: "Optional[threading.Lock]" = None,
        forming_port: Any = None,
        market_profile_port: Any = None,
    ) -> None:
        self._candle_port = candle_port
        self._compute_port = compute_port
        self._window_port = window_port
        self._is_known_ref = is_known_ref
        self.web_dir = Path(web_dir).resolve() if web_dir else None
        # 単一ソース共有: replay web_dir で miss したファイルを解決するフォールバック根
        #   （既定 <repo>/indigators/indicator_ui/web/js）。None のときフォールバック無効＝従来挙動。
        #   replay の複製が残る間は web_dir が優先されるため挙動不変（純増分・回帰ゼロ）。
        self.shared_js_root = Path(shared_js_root).resolve() if shared_js_root else None
        self._lock = heavy_lock if heavy_lock is not None else threading.Lock()
        # MP サブバー tick 逐次成長の Port（任意注入）。None のときは /market_profile_forming
        #   ルートを持たず静的配信へフォールバックする（既存 replay へ非干渉＝回帰ゼロ）。
        self._forming_port = forming_port
        self.forming_enabled = forming_port is not None
        # MP normal/sessions/replay（as-seen-at-t）の Port（任意注入）。None のときは /market_profile
        #   ルートを持たず静的配信へフォールバックする（既存 replay へ非干渉＝回帰ゼロ）。
        self._market_profile_port = market_profile_port
        self.market_profile_enabled = market_profile_port is not None

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

    def intraday(self, ref: str, start: int, end: int, mode: str, want_secs: bool = False) -> dict:
        # proto do_GET /intraday: 非 tick の未知 ref は事前に validation 拒否する。
        if self._is_known_ref is not None and ref != "jp225_tick" and not self._is_known_ref(ref):
            raise ValueError(f"unknown {ref}")
        req = IntrabarWindowRequest(ref=ref, start=start, end=end, mode=mode, want_secs=want_secs)
        with self._lock:  # ティック読込/集計を直列化（OOM 防止）
            res = intrabar_window(request=req, window_port=self._window_port)
        payload: dict = {"ok": res.ok, "m1": res.m1, "ticks": res.ticks}
        if res.m1_error is not None:
            payload["m1_error"] = res.m1_error
        if res.ticks_error is not None:
            payload["ticks_error"] = res.ticks_error
        # MP tick-live 用: want_secs かつ tick_secs があるときだけ並行配列を付与（secs 無は payload 不変）。
        if res.tick_secs:
            payload["tick_secs"] = res.tick_secs
        return payload

    def market_profile_forming(
        self, ref: str, timeframe: "str | None", now: "int | None",
        base: Any, since: Any, bins: Any, va: Any, barw: Any, frm: Any = None,
    ) -> "tuple[int, dict]":
        """MP サブバー tick 逐次成長データを返す（now は必ずリビール T＝因果・未来リーク防止）。

        ``frm``（任意・既定 None）: セッション窓 MP の base 累積下限 time（当日始まり=floor(now,86400)）。
        None は従来全期間 base（後方互換）。
        """
        req = MarketProfileFormingRequest(
            ref=ref, timeframe=timeframe, now=now, base=base, since=since, bins=bins, va=va,
            barw=barw, frm=frm,
        )
        with self._lock:  # forming 計算（dwell/resample）を直列化（OOM 防止）
            return market_profile_forming(request=req, forming_port=self._forming_port)

    def market_profile(
        self, ref: str, timeframe: "str | None", limit: Any, bins: Any, va: Any,
        src: Any, barw: Any, to: Any, frm: Any = None, today: Any = None,
        sessions: Any = None,
    ) -> "tuple[int, dict]":
        """MP normal/sessions/replay データを返す（to は必ずリビール T＝as-seen-at-t・未来リーク防止）。

        ``to`` 指定時は ``time<=to`` の足だけで集計する（因果）。``frm``/``today``/``sessions`` は
        増分2/日別分割の任意フラグ（None/省略は現行挙動）。
        """
        req = MarketProfileRequest(
            ref=ref, timeframe=timeframe, limit=limit, bins=bins, va=va, src=src,
            barw=barw, to=to, frm=frm, today=today, sessions=sessions,
        )
        with self._lock:  # profile 計算（candle/dwell resample）を直列化（OOM 防止）
            return market_profile(request=req, profile_port=self._market_profile_port)


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
                    return self._json(*nested_error("validation", str(e)[:200]))
                except Exception as e:  # noqa: BLE001
                    return self._json(*nested_error("internal", str(e)[:200]))
            if u.path == "/intraday":
                ref = (q.get("datasetRef") or ["jp225_m1"])[0]
                try:
                    start = int(q["start"][0])
                    end = int(q["end"][0])
                except Exception:  # noqa: BLE001
                    return self._json(*nested_error("validation", "start/end required"))
                mode = (q.get("mode") or ["real_ticks"])[0]
                want_secs = (q.get("secs") or [None])[0] == "1"  # MP tick-live gate（secs=1 のみ）
                try:
                    payload = app.intraday(ref, start, end, mode, want_secs=want_secs)
                    return self._json(200, payload)
                except ValueError as e:
                    return self._json(*nested_error("validation", str(e)[:200]))
                except Exception as e:  # noqa: BLE001
                    return self._json(*nested_error("internal", str(e)[:200]))
            if u.path == "/market_profile" and app.market_profile_enabled:
                ref = (q.get("datasetRef") or [None])[0]
                tf = (q.get("timeframe") or [None])[0]
                limit = (q.get("limit") or [None])[0]
                bins = (q.get("bins") or [None])[0]
                va = (q.get("va") or [None])[0]
                src = (q.get("src") or [None])[0]
                barw = (q.get("barw") or [None])[0]
                # to は必ずリビール T（as-seen-at-t）。省略時 None＝全期間（後方互換）。
                to = (q.get("to") or [None])[0]
                # from（ローリング窓下限）／today（スナップショット）／sessions（日別分割）。省略時 None。
                frm = (q.get("from") or [None])[0]
                today = (q.get("today") or [None])[0]
                sessions = (q.get("sessions") or [None])[0]
                try:
                    status, payload = app.market_profile(
                        ref, tf, limit, bins, va, src, barw, to,
                        frm=frm, today=today, sessions=sessions)
                    return self._json(status, payload)
                except Exception as e:  # noqa: BLE001
                    return self._json(*nested_error("internal", str(e)[:200]))
            if u.path == "/market_profile_forming" and app.forming_enabled:
                ref = (q.get("datasetRef") or [None])[0]
                tf = (q.get("timeframe") or [None])[0]
                since = (q.get("since") or [None])[0]
                base = (q.get("base") or [None])[0]
                now_raw = (q.get("now") or [None])[0]
                # now は必ずリビール T（因果）。数値でなければ None（controller が実時刻へフォールバックするが
                #   フロントは常に T を送るため実運用では常に T が入る）。
                now = int(now_raw) if (now_raw and now_raw.lstrip("-").isdigit()) else None
                bins = (q.get("bins") or [None])[0]
                va = (q.get("va") or [None])[0]
                barw = (q.get("barw") or [None])[0]
                # from（セッション窓 base 下限・当日始まり）。省略時 None＝従来全期間 base（後方互換）。
                frm = (q.get("from") or [None])[0]
                try:
                    status, payload = app.market_profile_forming(
                        ref, tf, now, base, since, bins, va, barw, frm)
                    return self._json(status, payload)
                except Exception as e:  # noqa: BLE001
                    return self._json(*nested_error("internal", str(e)[:200]))
            return self._serve_static(u.path)

        @staticmethod
        def _resolve_under(join_root: "Path | None", rel: str, allowed_roots):
            """``join_root/rel`` を解決し、dual-root ガードを通った実ファイルのみ返す。

            resolve() 後の実パスが ``allowed_roots``（web_dir / shared_js_root）のいずれかの
            配下にあり、かつ実ファイルのときのみ返す。単一ソース共有では web_dir/js 配下の
            シンボリックリンクが shared_js_root（=indicator_ui/web/js）を指すため、resolve()
            後は shared_js_root 配下になる。dual-root ガードにより web_dir 経由の一次解決で
            そのまま許可される（従来の名前一致フォールバック依存を排除）。
            パストラバーサル（``..``）は resolve 後に両ルート配下を外れるため弾かれる。
            join_root が None・全ルート不通過・非ファイルのときは None（呼び出し側が次ルート/404 へ）。
            """
            if join_root is None:
                return None
            fp = (join_root / rel).resolve()
            if not fp.is_file():
                return None
            for ar in allowed_roots:
                # 区切り境界一致（Path.is_relative_to, Python 3.9+）で CWE-22 を封じる。
                #   str.startswith は区切り境界を見ないため `.../replay_web` と接頭辞を共有する
                #   兄弟 `.../replay_web_SECRET` へ生 `..` で逸脱できてしまう（区切り境界なし
                #   prefix 一致）。is_relative_to は resolve() 後の実パスに対して境界単位で判定
                #   するため、正規の symlink（resolve 先が shared_js_root 配下）は許可され続ける。
                if ar is not None and fp.is_relative_to(ar):
                    return fp
            return None

        def _serve_static(self, path: str):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            # dual-root ガードで許可する根の集合。web_dir は replay の web 根全体（index.html/js/css/
            #   vendor + replay 固有）。共有元は **js/css/vendor サブツリーのみ**に限定する（最小権限）。
            #   shared_js_root（=indicator_ui/web）全体を許可すると build.mjs/package.json/data/tests/
            #   node_modules/prototype 等まで配信面に露出するため、資産3サブツリーだけを許可根にする。
            #   symlink 先（indicator_ui/web/{js,css,vendor}/…）は該当サブツリー配下で許可される。
            #   MP frontend は別モジュール（indigators/market_profile/web/js）へ切り出し済みで、
            #   replay の js/ 配下 symlink が MP モジュールを指す。resolve() 後は market_profile/web/js
            #   配下へ抜けるため、当該 js サブツリーのみを許可根に追加する（最小権限）。
            _mp_web_js = (
                app.shared_js_root.parents[1] / "market_profile" / "web" / "js"
                if app.shared_js_root else None
            )
            allowed = (
                app.web_dir,
                app.shared_js_root / "js" if app.shared_js_root else None,
                app.shared_js_root / "css" if app.shared_js_root else None,
                app.shared_js_root / "vendor" if app.shared_js_root else None,
                _mp_web_js,
            )
            # replay web_dir 優先。web_dir は web 根（index.html + js/ を含む）で URL の /js/ 接頭辞込みで解決。
            fp = self._resolve_under(app.web_dir, rel, allowed)
            # miss なら shared_js_root（=indicator_ui の web 根・js/css/vendor 包含）へ同一 rel で
            #   フォールバック。symlink 化した資産は web_dir 経由で一次解決されるため、本フォールバックは
            #   indicator_ui のみに実体があるファイル（replay に symlink も実体も無いもの）用。
            #   index.html は web_dir に実体があるため常に web_dir が優先され、共有元へは落ちない（per-app）。
            if fp is None:
                fp = self._resolve_under(app.shared_js_root, rel, allowed)
            if fp is None:
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
                return self._json(*nested_error("internal", "memory limit", generation=gen))
            except ValueError as e:
                return self._json(*nested_error("validation", str(e)[:200], generation=gen))
            except Exception as e:  # noqa: BLE001
                return self._json(*nested_error("internal", f"{type(e).__name__}: {str(e)[:200]}", generation=gen))
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
