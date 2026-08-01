"""serve_replay — 因果リビール再生バックエンドの HTTP フレームワーク層（proto_server 忠実）。

エンドポイント（proto と同一プロトコル）:
    GET  /candles?datasetRef=&timeframe=&limit=[&from=&pre=] → {ok, candles}
    GET  /available_days?datasetRef=&timeframe=             → {ok, days}
    POST /compute {indicatorId,variant,params,datasetRef,timeframe,limit,generation,mode,
                   untilTime,forming}                       → {ok, generation, series}
    GET  /intraday?datasetRef=&start=&end=&mode=            → {ok, m1, ticks[, *_error]}
    GET  /（静的）                                            → web_dir 配信（任意・no-store）

CLEAN_ARCH §6: HTTP・スレッド・静的配信という偶有的技術を最外層へ隔離する。R(rpy2) 非スレッド安全
＋巨大 resample の OOM 回避のため重い処理を 1 本の ``_HEAVY_LOCK`` で直列化する（proto と同一方針・
出力は不変）。エラー応答は正典契約 api_shared.http_contract（ERROR_STATUS・nested_error）に従う
（ISSUE-091 A2: 旧 proto 由来の独自形 {error:{type,message}}・internal→400 という契約分岐を是正。
例外翻訳は ValueError→validation / MemoryError・それ以外→internal）。ISSUE-097 🟡-4: 各ハンドラへ
個別コピーされていた例外分類を中央翻訳器 ``_error_response`` へ集約し、/market_profile・
/market_profile_forming に欠落していた ValueError→validation 分岐を正典契約へ是正した。
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

# 正典エラー契約（ISSUE-091 A2 / ISSUE-094 🔵-11）: status 翻訳・nested ボディとも中立共有
#   パッケージ api_shared.http_contract の単一定義を直参照する。
from api_shared.http_contract import nested_error

# 静的資産配信＋パストラバーサル防御（ISSUE-094 🟡-8: 殻から独立クラスへ抽出）。
from simulator.replay_ui.framework.static_file_server import StaticFileServer

from simulator.replay_ui.usecase.available_days import (
    AvailableDaysRequest,
    available_days,
)
from simulator.replay_ui.usecase.causal_compute import (
    CausalComputeRequest,
    CausalComputeSeqRequest,
    causal_compute,
    causal_compute_seq,
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


def _error_response(
    exc: Exception, *, generation: int = 0, message: "str | None" = None
) -> "tuple[int, dict[str, Any]]":
    """例外を正典 (status, nested body) へ翻訳する中央関数（ISSUE-097 🟡-4）。

    全 API ハンドラ共通の単一分類（旧: 各ハンドラへ個別コピーされていた
    ``except ValueError→validation / except Exception→internal`` を集約）:
        ValueError            → validation（400）
        MemoryError・その他    → internal（500）
    status 表引き・nested ボディ整形は api_shared.http_contract の単一定義
    （``ERROR_STATUS`` / ``nested_error``）へ委譲する。``message`` 省略時は ``str(exc)[:200]``。
    新エラー種別の追加は本関数 1 箇所の編集で全ハンドラへ反映される（OCP: 最大 5 ブロックの
    同期編集を解消）。
    """
    error_type = "validation" if isinstance(exc, ValueError) else "internal"
    if message is None:
        message = str(exc)[:200]
    return nested_error(error_type, message, generation=generation)


class _HeavyWorker:
    """重い処理を専用スレッド 1 本で直列実行するワーカー（ISSUE-156・ライブ ISSUE-155 と同一設計）。

    rpy2/R はスレッド親和（常に同一スレッドからの呼び出しが必要）のため、ロック直列だけでは
    リクエストごとに実行スレッドが変わる ThreadingHTTPServer 下で安全性が保証されない。
    本ワーカーが heavy 経路（candles resample / compute / intraday）を常に同一スレッドで実行する。
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="replay-heavy-worker", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            fn, done, box = self._q.get()
            try:
                box["result"] = fn()
            except BaseException as exc:  # noqa: BLE001（呼び出し側スレッドへ再送出）
                box["error"] = exc
            finally:
                done.set()

    def run(self, fn):
        done = threading.Event()
        box: dict = {}
        self._q.put((fn, done, box))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")


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
        days_port: Any = None,
    ) -> None:
        self._candle_port = candle_port
        # カレンダー（再生開始日）の選択可能日を返す Port。None のとき /available_days ルートを
        #   持たず静的配信へフォールバックする（既存 replay へ非干渉＝回帰ゼロ）。
        self._days_port = days_port
        self.available_days_enabled = days_port is not None
        self._compute_port = compute_port
        self._window_port = window_port
        self._is_known_ref = is_known_ref
        self.web_dir = Path(web_dir).resolve() if web_dir else None
        # 単一ソース共有: replay web_dir で miss したファイルを解決するフォールバック根
        #   （既定 <repo>/indigators/indicator_ui/web/js）。None のときフォールバック無効＝従来挙動。
        #   replay の複製が残る間は web_dir が優先されるため挙動不変（純増分・回帰ゼロ）。
        self.shared_js_root = Path(shared_js_root).resolve() if shared_js_root else None
        # 静的配信＋トラバーサル防御は StaticFileServer へ委譲（ISSUE-094 🟡-8）。許可根は
        #   web_dir / shared_js_root から本クラス内で導出する（配信面・応答 byte は不変）。
        self.static_server = StaticFileServer(self.web_dir, self.shared_js_root)
        self._lock = heavy_lock if heavy_lock is not None else threading.Lock()
        # ISSUE-156（H）: 重い処理をロック直列に加えて「常に同一スレッド」で実行する専用ワーカー。
        #   ロックだけでは rpy2/R（スレッド親和＝同一スレッドからの呼び出しが必要）の安全性が
        #   保証されないため、ライブサーバ（indicator_ui ISSUE-155）と同一設計へ統一する。
        #   既存の heavy_lock 注入 API・ロックの意味（外部共有直列化）は温存（ワーカー内でも取得）。
        self._heavy_worker = _HeavyWorker()
        # MP サブバー tick 逐次成長の Port（任意注入）。None のときは /market_profile_forming
        #   ルートを持たず静的配信へフォールバックする（既存 replay へ非干渉＝回帰ゼロ）。
        self._forming_port = forming_port
        self.forming_enabled = forming_port is not None
        # MP normal/sessions/replay（as-seen-at-t）の Port（任意注入）。None のときは /market_profile
        #   ルートを持たず静的配信へフォールバックする（既存 replay へ非干渉＝回帰ゼロ）。
        self._market_profile_port = market_profile_port
        self.market_profile_enabled = market_profile_port is not None

    def candles(
        self,
        ref: str,
        tf: "str | None",
        limit: "int | None",
        start: "int | None" = None,
        pre: int = 0,
    ) -> "list[dict]":
        req = RevealCandlesRequest(ref=ref, timeframe=tf, limit=limit, start=start, pre=pre)
        def _run():
            # 巨大 resample を直列化（並行多重で OOM 防止）。
            # ISSUE-036(a): 非 tick の軽量経路も同じ錠の内側に置いている（proto は tick のみ施錠）。
            #   出力は変わらず**保守的な直列化**であり、意図的に据え置く:
            #     - /candles は timeframe により resample の有無が実行時に決まるため、呼び出し前に
            #       「軽量である」と判定できない（判定を足すと分岐が二重管理になる）。
            #     - 並行実行のメリットが実測されていない。緩めるならまず所要時間を計測し、
            #       OOM 耐性が落ちないことを確認してから行う（未実施）。
            with self._lock:
                return reveal_candles(request=req, candle_port=self._candle_port)
        return self._heavy_worker.run(_run)

    def available_days(self, ref: str, tf: "str | None") -> "list[str]":
        req = AvailableDaysRequest(ref=ref, timeframe=tf)
        def _run():
            with self._lock:  # 全期間 index 走査を直列化（巨大 1m でも OOM 防止）
                return available_days(request=req, days_port=self._days_port)
        return self._heavy_worker.run(_run)

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
        def _run():
            with self._lock:  # R(rpy2) 非スレッド安全＋メモリのため直列化
                return causal_compute(request=req, compute_port=self._compute_port)
        return self._heavy_worker.run(_run)

    def compute_seq(self, body: dict) -> "list[list[dict]]":
        """POST /compute mode='latest_seq' — 足内推移の各時点の latest を一括で返す（ISSUE-232）。

        既存 ``compute``（単発）とは別メソッドに分ける（既存経路の分岐を増やさない＝挙動不変）。
        直列化・heavy worker の扱いは ``compute`` と同一（R/rpy2 のスレッド親和とメモリのため）。
        """
        req = CausalComputeSeqRequest(
            indicator=body.get("indicatorId"),
            variant=body.get("variant", "default"),
            ref=body.get("datasetRef"),
            timeframe=body.get("timeframe"),
            limit=body.get("limit"),
            until_time=body.get("untilTime"),
            forming_seq=body.get("formingSeq") or [],
            params=dict(body.get("params") or {}),
        )
        def _run():
            with self._lock:  # R(rpy2) 非スレッド安全＋メモリのため直列化
                return causal_compute_seq(request=req, compute_port=self._compute_port)
        return self._heavy_worker.run(_run)

    def intraday(self, ref: str, start: int, end: int, mode: str, want_secs: bool = False) -> dict:
        # proto do_GET /intraday: 非 tick の未知 ref は事前に validation 拒否する。
        if self._is_known_ref is not None and ref != "jp225_tick" and not self._is_known_ref(ref):
            raise ValueError(f"unknown {ref}")
        req = IntrabarWindowRequest(ref=ref, start=start, end=end, mode=mode, want_secs=want_secs)
        def _run():
            with self._lock:  # ティック読込/集計を直列化（OOM 防止）
                return intrabar_window(request=req, window_port=self._window_port)
        res = self._heavy_worker.run(_run)
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

        ``to`` 指定時は ``time<=to`` の足だけで集計する（因果）。``to`` はリプレイの単一時計
        （リビール秒粒度・ISSUE-129: zp は now=to として現在時刻に読む）。``frm``/``today``/
        ``sessions`` は増分2/日別分割の任意フラグ（None/省略は現行挙動）。
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
                # カレンダー選択（再生開始日）用の窓指定。未指定は従来の tail(limit)＝挙動不変。
                try:
                    start = int(q["from"][0]) if "from" in q else None
                    pre = int(q["pre"][0]) if "pre" in q else 0
                except Exception:  # noqa: BLE001
                    return self._json(*nested_error("validation", "from/pre must be int"))
                try:
                    candles = app.candles(ref, tf, lim, start=start, pre=pre)
                    return self._json(200, {"ok": True, "candles": candles})
                except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
                    return self._json(*_error_response(e))
            if u.path == "/available_days" and app.available_days_enabled:
                ref = (q.get("datasetRef") or ["jp225_m1"])[0]
                tf = (q.get("timeframe") or [None])[0]
                try:
                    return self._json(200, {"ok": True, "days": app.available_days(ref, tf)})
                except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
                    return self._json(*_error_response(e))
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
                except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
                    return self._json(*_error_response(e))
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
                except Exception as e:  # noqa: BLE001 — ValueError→validation 欠落を是正し中央翻訳へ集約（ISSUE-097 🟡-4）
                    return self._json(*_error_response(e))
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
                except Exception as e:  # noqa: BLE001 — ValueError→validation 欠落を是正し中央翻訳へ集約（ISSUE-097 🟡-4）
                    return self._json(*_error_response(e))
            # 静的配信＋トラバーサル防御は StaticFileServer へ委譲（Handler は API ルーティング＋
            #   委譲のみ・ISSUE-094 🟡-8）。許可根の導出・dual-root ガード・応答 byte は不変。
            return app.static_server.serve(self, u.path)

        def do_POST(self):  # noqa: N802
            if urlparse(self.path).path != "/compute":
                self.send_response(404)
                self.end_headers()
                return
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            gen = body.get("generation", 0)
            # ISSUE-232: 足内一括計算（mode='latest_seq'）。応答キーは steps（series とは別キー＝
            #   既存クライアントの読み取り面に影響しない）。エラー翻訳は既存と同一の中央経路。
            if body.get("mode") == "latest_seq":
                try:
                    steps = app.compute_seq(body)
                except MemoryError as e:
                    return self._json(*_error_response(e, generation=gen, message="memory limit"))
                except ValueError as e:
                    return self._json(*_error_response(e, generation=gen))
                except Exception as e:  # noqa: BLE001
                    return self._json(*_error_response(
                        e, generation=gen, message=f"{type(e).__name__}: {str(e)[:200]}"))
                return self._json(200, {"ok": True, "generation": gen, "steps": steps})
            try:
                series = app.compute(body)
            # 分類（status/type）は _error_response へ集約（ISSUE-097 🟡-4）。except ブロックは
            #   compute 固有のメッセージ（MemoryError→"memory limit"・generic→"Name: msg"）供給のみ。
            except MemoryError as e:
                return self._json(*_error_response(e, generation=gen, message="memory limit"))
            except ValueError as e:
                return self._json(*_error_response(e, generation=gen))
            except Exception as e:  # noqa: BLE001
                return self._json(*_error_response(
                    e, generation=gen, message=f"{type(e).__name__}: {str(e)[:200]}"))
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
