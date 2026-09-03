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
    causal_compute_seq_multi,
    CausalComputeSeqMultiRequest,
    CausalComputeSeqSpec,
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
from simulator.replay_ui.usecase.tickvol_profile import (
    TickvolProfileRequest,
    tickvol_profile,
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
    # ISSUE-284: 例外が**自分で分類を宣言している**なら、それを尊重する。
    #   指標計算は ``ComputeError``（``error_type`` / ``message`` を持つ＝ComputeErrorPort）で
    #   「入力条件を満たしていない」ことを validation として申告する。Python の例外型（ValueError か
    #   否か）で分類していたため、これが **internal 500** に化けていた（実測: cvfe の
    #   E01_INSUFFICIENT_BARS が /replay/compute では 500・/live/compute では 400）。
    #   500 は「サーバ内部の異常」を意味し、監視と切り分けを誤らせる。宣言があるものは宣言に従う。
    declared = getattr(exc, "error_type", None)
    declared_message = getattr(exc, "message", None)
    if isinstance(declared, str) and declared:
        # 宣言済みの分類・メッセージは呼び出し側の汎用文言（"Name: msg"）で上書きしない。
        text = declared_message if isinstance(declared_message, str) and declared_message else str(exc)
        # ISSUE-283: 指標が申告した機械可読診断（requiredBars 等）をそのまま運ぶ。
        return nested_error(declared, text[:200], generation=generation,
                            violations=getattr(exc, "violations", None))
    error_type = "validation" if isinstance(exc, ValueError) else "internal"
    if message is None:
        message = str(exc)[:200]
    return nested_error(error_type, message, generation=generation)


def write_replay_json(handler: Any, response: "tuple[int, Any]") -> None:
    """replay の JSON 応答を書き出す（応答 byte の**唯一の定義**・ISSUE-479 Wave2 3-4）。

    ``response`` は ``(status, payload)``。ヘッダは sim 側（json_get_routes の write_json）と
    **異なる**——``Content-Type`` に charset を付けず、``json.dumps`` の既定（ensure_ascii=True）で
    符号化する。front と front の検定が見ているのはこの byte 列なので、骨格を共有する
    ついでに統一してはならない。統一が必要になったら、それは応答仕様の変更として別に扱う。

    機能別 App へ分割しても応答が 1 バイトも変わらないよう、書き出しは本関数 1 箇所に閉じる
    （Handler の ``_json`` も本関数へ委譲する）。
    """
    status, payload = response
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class _QueryStrippingStatic:
    """静的配信の直前でクエリを落とす終端層（ISSUE-479 Wave2 3-4）。

    ルート応答器は「転送する値を書き換えない」——数珠つなぎにしたとき内側のルートが
    クエリを失うためである（実測: ``/intraday?start=..&end=..`` が 400 になった）。
    そのぶん、クエリを落とす責務をここ 1 箇所に置く。`StaticFileServer` は ``?`` を
    含む path を解決できず、渡せば静かに 404 になる。

    `StaticFileServer` そのものは触らない（応答 byte・許可根・CWE-22 防御は単一ソースのまま）。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def serve(self, handler: Any, path: str) -> None:
        return self._inner.serve(handler, path.split("?", 1)[0])

    def __getattr__(self, name: str) -> Any:
        """解決・許可根などの面は内側の単一ソースへ委譲する。"""
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)


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
        tickvol_profile_port: Any = None,
        catalog_port: Any = None,
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
        # 取引密度ハイライト（時刻帯の背景色）の Port（任意注入）。None のときは /tickvol_profile
        #   ルートを持たず静的配信へフォールバックする（既存 replay へ非干渉＝回帰ゼロ）。
        self._tickvol_profile_port = tickvol_profile_port
        self.tickvol_profile_enabled = tickvol_profile_port is not None
        # 指標 param スキーマ（既定値＋variant ごとの受理 param）の Port（任意注入）。None のときは
        #   /catalog ルートを持たず静的配信へフォールバックする（既存 replay へ非干渉）。
        #   ISSUE-278 #8/#4: この経路が無いと front は variant が受理しない param を送り、
        #   ライブ側 back のフェイルクローズで validation エラーになる。
        self._catalog_port = catalog_port
        self.catalog_enabled = catalog_port is not None
        # クエリを落とす終端層（ルート応答器は転送値を書き換えないため・上の理由参照）。
        self.static_server = _QueryStrippingStatic(self.static_server)
        # ISSUE-479 Wave2 3-4（S-3）: GET のルーティングは機能別 App が持つ。ここで組み立てた
        #   ルート表を自分の配信面に据えることで、Handler は分岐を 1 つも持たなくなる。
        #   どのルートが存在するかは `build_replay_routes` だけが決める（上の *_enabled を読む）。
        #   フォールバックの末端は上で作った `StaticFileServer` そのもので、静的配信の
        #   応答 byte・許可根・CWE-22 防御は単一ソースのまま変わらない。
        self.static_server = build_replay_routes(self).static_server

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
            win_start=body.get("winStart"),
            win_end=body.get("winEnd"),
            # ISSUE-287: 上位足計算（計算.時間足）。従来は受け取らず無言で C 足計算していた。
            compute_timeframe=body.get("computeTimeframe"),
        )
        def _run():
            with self._lock:  # R(rpy2) 非スレッド安全＋メモリのため直列化
                return causal_compute(request=req, compute_port=self._compute_port,
                                      window_port=self._window_port)
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
            win_start=body.get("winStart"),
            win_end=body.get("winEnd"),
            # ISSUE-290: 足内一括計算も計算足（計算.時間足）を受け取る。
            compute_timeframe=body.get("computeTimeframe"),
        )
        def _run():
            with self._lock:  # R(rpy2) 非スレッド安全＋メモリのため直列化
                return causal_compute_seq(request=req, compute_port=self._compute_port,
                                          window_port=self._window_port)
        return self._heavy_worker.run(_run)

    def compute_seq_multi(self, body: dict) -> "dict[str, list[list[dict]]]":
        """POST /compute mode='latest_seq_multi' — 複数指標の足内一括計算（ISSUE-300）。

        ``compute_seq`` を指標ごとに呼ぶのと同値で、共有できる仕事（C 窓のロード・実 tick 数の
        読み取り・計算足ごとの H 窓素材）を 1 回に畳む。直列化・heavy worker の扱いは既存と同一。
        """
        req = CausalComputeSeqMultiRequest(
            ref=body.get("datasetRef"),
            timeframe=body.get("timeframe"),
            limit=body.get("limit"),
            until_time=body.get("untilTime"),
            forming_seq=body.get("formingSeq") or [],
            win_start=body.get("winStart"),
            win_end=body.get("winEnd"),
            specs=[
                CausalComputeSeqSpec(
                    instance_id=s.get("instanceId"),
                    indicator=s.get("indicatorId"),
                    variant=s.get("variant", "default"),
                    params=dict(s.get("params") or {}),
                    compute_timeframe=s.get("computeTimeframe"),
                )
                for s in (body.get("specs") or [])
            ],
        )
        def _run():
            with self._lock:  # R(rpy2) 非スレッド安全＋メモリのため直列化
                return causal_compute_seq_multi(request=req, compute_port=self._compute_port,
                                                window_port=self._window_port)
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

    def tickvol_profile(
        self, ref: str, sessions: Any = None, pct: Any = None, until: Any = None
    ) -> "tuple[int, dict]":
        """取引密度の時刻帯プロファイル（背景色帯）を返す。

        ``until`` は必ずリビール T（単一時計 to）を渡す。``until`` が属するセッション日は集計に
        含まれない（当日非参照＝因果・未来リーク防止）。
        """
        req = TickvolProfileRequest(ref=ref, sessions=sessions, pct=pct, until=until)
        with self._lock:  # 1 分足全期間の集計を直列化（OOM 防止・他の重い処理と同規律）
            return tickvol_profile(request=req, profile_port=self._tickvol_profile_port)

    def catalog(self) -> "tuple[int, dict]":
        """指標 param の既定値と variant ごとの受理 param（paramScopes）を返す。

        入力を持たず（dict の deep copy のみ）計算も伴わないため、重い処理の直列化錠は取らない。
        実体はライブ側 controller（``handle_catalog``）＝ライブと応答が byte 一致する。
        """
        return self._catalog_port.catalog()


def build_replay_routes(inner: Any) -> Any:
    """機能別ルート App を連結して返す（ISSUE-479 Wave2 3-4・ルート構成の唯一の宣言）。

    各 App は内側を包み、自分のルートを JSON 経路として ``static_server`` の前へ挟む。
    外れた path は内側の ``static_server`` へ落ち、最終的に `StaticFileServer` に着く。
    どのルートが存在するかは**この関数だけ**が決める（Handler は分岐を持たない）。

    import を関数内に置くのは、各ルート App が本モジュールの `write_replay_json` /
    `_error_response` を参照するためである（module-level import にすると循環になる）。

    呼ぶのは `ReplayApp.__init__` の末尾 1 箇所である。ルート構成を外（合成根）へ出さないのは、
    ``ReplayApp`` を組んだだけで API ルートを持たない殻ができてしまうと、結線し忘れが
    「受け口はあるのに無言で 404」という形で表に出るからである（ISSUE-291 と同型の壊れ方）。
    差し替えたいときは本関数を差し替える（ルートの宣言は依然としてここ 1 箇所だけ）。

    重い処理のワーカーとロックは ``inner`` の**単一インスタンスを全 App が共有**する。
    各 App は自前で作らず属性委譲で内側のものを引く——rpy2/R はスレッド親和で、App ごとに
    ワーカーを持つと「常に同一スレッドで実行する」という前提が壊れるからである（絶対条件）。
    """
    from simulator.replay_ui.framework.serve_replay_candles import ReplayCandlesApp
    from simulator.replay_ui.framework.serve_replay_catalog import ReplayCatalogApp
    from simulator.replay_ui.framework.serve_replay_intraday import ReplayIntradayApp
    from simulator.replay_ui.framework.serve_replay_profiles import ReplayProfilesApp

    app = ReplayCandlesApp(inner=inner)
    app = ReplayIntradayApp(inner=app)
    app = ReplayProfilesApp(inner=app)
    return ReplayCatalogApp(inner=app)


def make_handler(app: ReplayApp):
    """``app`` を束ねた BaseHTTPRequestHandler サブクラスを返す（proto H 忠実）。"""
    # import を関数内に置くのは、本モジュールの `_error_response` を参照するためである
    #   （module-level import にすると循環になる）。
    from simulator.replay_ui.framework.serve_replay_compute import ReplayComputeApp

    compute_app = ReplayComputeApp(inner=app)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, obj: Any) -> None:
            """応答の書き出しは `write_replay_json` の単一定義へ委譲する（byte 不変）。"""
            write_replay_json(self, (code, obj))

        def log_message(self, *a):  # noqa: D401 — アクセスログ抑制（proto と同一）
            pass

        def do_GET(self):  # noqa: N802
            """ルーティングは ``app.static_server`` が唯一決める（ISSUE-479 Wave2 3-4）。

            JSON ルート列（機能別 App が前置きしたもの）→ 静的配信、の順で解決される。
            クエリ付きの path をそのまま渡す: ルートはクエリを読み、静的配信へ落とす前に
            ルート応答器がクエリを落とす（静的解決へクエリは届かない）。
            """
            return app.static_server.serve(self, self.path)

        def do_POST(self):  # noqa: N802
            """/compute だけを受ける。モードの差と例外分類は `ReplayComputeApp` が持つ。

            分割前はここに 3 モード × 3 分類＝9 つの except ブロックが並んでいた
            （ISSUE-479 Wave2 3-5）。
            """
            if urlparse(self.path).path != "/compute":
                self.send_response(404)
                self.end_headers()
                return
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            return self._json(*compute_app.respond(body))

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
