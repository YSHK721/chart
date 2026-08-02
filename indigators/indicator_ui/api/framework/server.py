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
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse


class _ComputeWorker:
    """重い計算を専用スレッド 1 本で直列実行するワーカー（ISSUE-155）。

    背景: 旧実装は単一スレッド HTTPServer で全リクエストを直列化していた（rpy2/R が
    スレッド非安全のため）。その結果、重い /compute の背後に静的 JS・/candles まで並び、
    ページ起動が数秒〜ハング相当まで遅延した（別タブ併用時に顕著）。

    本ワーカーは「重い計算だけを常に同一スレッドで直列実行」する。rpy2/R はスレッド親和
    （同一スレッドからの呼び出しなら安全）のため旧実装と同じ安全性を保ちながら、静的配信・
    /candles・/live_ticks 等の軽量応答は ThreadingHTTPServer で並行化できる。
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="compute-worker", daemon=True)
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
        """fn をワーカースレッドで実行し、結果を返す（例外は呼び出し側へ再送出）。"""
        done = threading.Event()
        box: dict = {}
        self._q.put((fn, done, box))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")


_COMPUTE_WORKER = _ComputeWorker()

# ISSUE-156（B/A）: ワーカー分離とプール化。
#   - _COMPUTE_WORKER: rpy2/R スレッド親和が必要な tgp_btlm 専用（従来どおり単一スレッド固定）。
#   - _MP_WORKER: Market Profile 系 GET 専用（重い zp 計算が指標計算をブロックしない分離・
#     MP 内部状態は単一ワーカーで従来どおり直列）。
#   - _COMPUTE_POOL: rpy2 非依存の指標 /compute 用プール（純 numpy/pandas＝スレッド安全。
#     動的モジュールロードは call_binding 側の import ロック、データ供給は serving_cache 側の
#     ロックで保護）。GIL 下でも numpy の C 区間が並列化され、指標間のレイテンシが重ならない。
_MP_WORKER = _ComputeWorker()
_COMPUTE_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="compute-pool")

# ISSUE-087 🟡-3（正規化）: 固有名パッケージ（marketdata / market_profile_api）の恒久解決は
#   venv の .pth（tools/install_dev_paths.py）が担う。本殻はエントリポイントとして
#   **自スライスの api/ のみ**を結線する（汎用名 adapter/framework は他スライスと衝突するため
#   .pth に載せない＝entry でのみ解決）。.pth 未登録環境（新規 venv 等）ではフォールバックで
#   従来どおり自己結線する（自己完結起動の温存・fresh clone を壊さない）。
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))
try:  # .pth 登録済みなら不要（正規経路）。
    import marketdata  # noqa: F401
    import market_profile_api  # noqa: F401
except ImportError:  # フォールバック（未登録環境の自己完結起動）。
    _MP_API_ROOT = _API_ROOT.parents[1] / "market_profile" / "api"
    if str(_MP_API_ROOT) not in sys.path:
        sys.path.insert(0, str(_MP_API_ROOT))
    _REPO_ROOT = _API_ROOT.parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from marketdata import dataset  # noqa: E402
from api_shared import http_contract as _contract  # noqa: E402  (nested_error 単一定義・ISSUE-094 🔵-11)
from adapter.compute import forming_bar as forming_bar_mod  # noqa: E402
from adapter.compute.call_binding import requires_dedicated_worker  # noqa: E402
from adapter.controller.compute_controller import handle_compute  # noqa: E402
from market_profile_api.controller.market_profile_controller import handle_market_profile  # noqa: E402
from market_profile_api.controller.market_profile_forming_controller import (  # noqa: E402
    augment_forming_payload,
    handle_market_profile_forming,
)
from market_profile_api.controller.tf_period_profile_controller import (  # noqa: E402
    handle_tf_period_profile,
)
from adapter.controller.candles_controller import (  # noqa: E402
    handle_candles,
    handle_forming_bar,
)
from adapter.controller.catalog_controller import handle_catalog  # noqa: E402
from adapter.controller.tickvol_profile_controller import (  # noqa: E402
    handle_tickvol_profile,
)
from adapter.gateway.composition import install_default_ports  # noqa: E402

# ISSUE-183（DIP）: 本モジュールが真の Composition Root。usecase の Output Boundary
#   （DatasetPort）へ既定 factory を **起動時に 1 回** 登録する。これによりポート側から
#   ``adapter.gateway.composition`` を pull する遅延 import（内側 → 外側の逆流）を撤去できる。
install_default_ports()

# 静的配信ルート（web/）。api/ → parents[1]=api → parents[2]=indicator_ui → web。
_WEB_ROOT = (_API_ROOT.parent / "web").resolve()

# MP frontend は別モジュール（indigators/market_profile/web/js）へ切り出し済み。present は MP を
# 「利用する側」で、web/js 配下に MP モジュール実体を指す symlink を持つ。resolve() 後の実パスは
# MP モジュールの js/ サブツリーへ抜けるため、配信許可根を web/ ∪ market_profile/web/js の
# dual-root（is_relative_to 境界一致）へ拡張する。許可は js/ サブツリーに限定＝最小権限
# （build.mjs/package.json/tests 等は露出しない）。パストラバーサルは is_relative_to で封じる。
_MP_WEB_JS_ROOT = (_API_ROOT.parents[1] / "market_profile" / "web" / "js").resolve()

# POST 本文サイズ上限（§7.3・1 MiB）。超過は 413 で拒否する。
_MAX_BODY_BYTES = 1 * 1024 * 1024

# ライブ tick バッファ（ISSUE-049・配信系）。served（B方式）起動時に serve() が生成・start() する。
# テストは set_live_tick_buffer でフェイクを注入する／None のままなら /live_ticks は空を返す
# （自動起動なし＝ネットワーク非依存）。記録系（parquet/M1/rollups）へは一切干渉しない（メモリのみ）。
_live_tick_buffer: Optional[Any] = None


def set_live_tick_buffer(buffer: Optional[Any]) -> None:
    """/live_ticks が配信する LiveTickBuffer を差し替える（注入点・テストで fake/None を渡す）。"""
    global _live_tick_buffer
    _live_tick_buffer = buffer



# ISSUE-087 🟡-1: _forming_bar_from_buffer は adapter/controller/candles_controller へ移設（薄殻化）。
# ISSUE-094 🟡-8: MP forming payload への buffer tick 合成（旧 _augment_mp_forming_ticks・業務判断）は
#   MP 側 controller の augment_forming_payload へ移設。殻は buffer を引数で渡すだけ（_handle_market_profile_forming）。

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
    """§6.3.4 nested エラーボディ（殻の例外・候補外要求も同形で返す）。

    ボディ形は正典 api_shared.http_contract.nested_error の単一定義へ委譲（ISSUE-094 🔵-11）。
    ステータスはエンドポイント固有の判断（404/413 等）があるため呼び出し側が選ぶ。
    """
    return _contract.nested_error(error_type, message, generation=generation)[1]


def _resolve_static(url_path: str) -> Path | None:
    """URL パスを web/ ルート内の実ファイルへ解決する（パストラバーサル防止）。

    ``/`` は index.html へ。正規化後に web/ ルート外を指す場合・存在しない場合は None。
    """
    rel = url_path.lstrip("/")
    if rel == "":
        rel = "index.html"
    # 正規化（``..`` を解決）した上で web/ ルート内かを厳密判定する。symlink は resolve() で
    #   実体へ解決され、MP モジュール（market_profile/web/js）へ抜ける場合も dual-root の
    #   is_relative_to 境界一致で許可する（区切り境界単位・CWE-22 封じ）。
    candidate = (_WEB_ROOT / rel).resolve()
    if not (
        candidate.is_relative_to(_WEB_ROOT)
        or candidate.is_relative_to(_MP_WEB_JS_ROOT)
    ):
        # 両ルート外（``..`` 等で外へ抜けた）→ 拒否。
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
            # ISSUE-155/156: スレッド親和必須（rpy2/R 等）の指標は専用ワーカーで単一スレッド固定、
            #   それ以外の指標は純 numpy/pandas のためプールで並列実行（指標間のレイテンシが
            #   重ならない）。応答書き出しはリクエストスレッド側。どの指標が親和必須かは
            #   call_binding の宣言（thread_affinity）が唯一の真実源＝本殻は指標名を知らない
            #   （SOLID 是正 🔴-3・OCP: 親和指標の追加はテーブル宣言のみで完結する）。
            if requires_dedicated_worker(body.get("indicatorId")):
                status, payload = _COMPUTE_WORKER.run(lambda: handle_compute(body))
            else:
                status, payload = _COMPUTE_POOL.submit(handle_compute, body).result()
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
            _MP_WORKER.run(lambda: self._handle_market_profile(parse_qs(parsed.query)))
            return
        if parsed.path == "/market_profile_forming":
            _MP_WORKER.run(lambda: self._handle_market_profile_forming(parse_qs(parsed.query)))
            return
        if parsed.path == "/tf_period_profile":
            _MP_WORKER.run(lambda: self._handle_tf_period_profile(parse_qs(parsed.query)))
            return
        if parsed.path == "/live_ticks":
            self._handle_live_ticks(parse_qs(parsed.query))
            return
        if parsed.path == "/tickvol_profile":
            self._handle_tickvol_profile(parse_qs(parsed.query))
            return
        if parsed.path == "/catalog":
            self._handle_catalog()
            return
        self._handle_static(parsed.path)

    def _handle_tickvol_profile(self, query: dict[str, list[str]]) -> None:
        """GET /tickvol_profile — 取引密度の時刻帯プロファイル（背景色帯の唯一源）を配信する薄殻。

        検証・集計は handle_tickvol_profile（純ロジック）へ委譲する（/candles と同規律）。
        """
        ref = (query.get("datasetRef") or [None])[0]
        sessions = (query.get("sessions") or [None])[0]
        pct = (query.get("pct") or [None])[0]
        until = (query.get("until") or [None])[0]
        status, payload = handle_tickvol_profile(ref, sessions, pct, until)
        self._send_json(status, payload)

    def _handle_catalog(self) -> None:
        """GET /catalog — param 既定値スキーマ（単一情報源）を配信する薄殻（ISSUE-092 ③）。

        検証・組み立ては純ロジック ``handle_catalog`` に委譲し、本メソッドは (status, payload) の
        JSON 応答のみを担う。クエリ非依存（全指標の既定値スキーマを一括返却）。
        """
        status, payload = handle_catalog()
        self._send_json(status, payload)

    def _handle_candles(self, query: dict[str, list[str]]) -> None:
        """GET /candles — 検証・生成は handle_candles（純ロジック）へ委譲する薄殻（ISSUE-087 🟡-1）。"""
        ref = (query.get("datasetRef") or [None])[0]
        timeframe = (query.get("timeframe") or [None])[0]
        limit_raw = (query.get("limit") or [None])[0]
        status, payload = handle_candles(ref, timeframe, limit_raw)
        self._send_json(status, payload)

    def _handle_forming_bar(self, query: dict[str, list[str]]) -> None:
        """GET /forming_bar — 検証・3段フォールバックは handle_forming_bar（純ロジック）へ委譲する薄殻。"""
        ref = (query.get("datasetRef") or [None])[0]
        timeframe = (query.get("timeframe") or [None])[0]
        now_raw = (query.get("now") or [None])[0]
        status, payload = handle_forming_bar(ref, timeframe, now_raw, buffer=_live_tick_buffer)
        self._send_json(status, payload)

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
        sessions = (query.get("sessions") or [None])[0]  # 日別プロファイル分割（'1' で sessions[] 付加）。移植元 prototype_260630-01。
        try:
            status, payload = handle_market_profile(
                ref, timeframe, limit, bins, va, src, barw, to,
                **{"from": frm, "today": today, "sessions": sessions},
            )
        except Exception as exc:  # noqa: BLE001（殻の最後の砦・nested で返す）
            self._send_json(500, _nested_error("internal", f"market_profile 取得に失敗しました: {exc}"))
            return
        self._send_json(status, payload)

    def _handle_market_profile_forming(self, query: dict[str, list[str]]) -> None:
        """GET /market_profile_forming — MP サブバー tick 逐次成長の base+forming tick 列+active table（読取のみ）。

        検証（非 tick ref / 非対応 tf は 400）・組み立ては純ロジック
        ``handle_market_profile_forming`` に委譲し、本メソッドはクエリ取り出しと (status, payload) の
        JSON 応答のみを担う（``_handle_market_profile`` と同型の薄殻）。
        """
        ref = (query.get("datasetRef") or [None])[0]
        timeframe = (query.get("timeframe") or [None])[0]
        since = (query.get("since") or [None])[0]
        base = (query.get("base") or [None])[0]
        now_raw = (query.get("now") or [None])[0]
        now_override = int(now_raw) if (now_raw and now_raw.lstrip("-").isdigit()) else None
        bins = (query.get("bins") or [None])[0]
        va = (query.get("va") or [None])[0]
        barw = (query.get("barw") or [None])[0]
        # セッション窓 MP の base 累積下限 time（UNIX 秒・省略時=全期間＝後方互換）。兄弟の
        #   _handle_market_profile と同型で controller へ透過する。これが欠けると from_ts=None に
        #   落ち、base レンジが全期間 low/high（例 2012 年安値）へ広がり当日成長が不可視になる。
        frm = (query.get("from") or [None])[0]
        try:
            status, payload = handle_market_profile_forming(
                ref, timeframe, since, base, now_override, bins, va, barw, frm=frm,
            )
        except Exception as exc:  # noqa: BLE001（殻の最後の砦・nested で返す）
            self._send_json(
                500, _nested_error("internal", f"market_profile_forming 取得に失敗しました: {exc}"))
            return
        # 秒成長の遅延解消: forming 期間の ticks を in-memory buffer（near-real-time）で補完する
        #   （parquet フロンティア遅延で欠ける現在分の末尾 tick を埋める）。ok 応答のみ・非破壊。
        if status == 200 and isinstance(payload, dict) and payload.get("ok"):
            # 殻はバッファを渡すだけ。対応判定・合成は MP 側 controller が担う（ISSUE-094 🟡-8）。
            augment_forming_payload(payload, ref, timeframe, since, buffer=_live_tick_buffer)
        self._send_json(status, payload)

    def _handle_tf_period_profile(self, query: dict[str, list[str]]) -> None:
        """GET /tf_period_profile — 時間足毎の最小価格単位プロファイル列（ローリング窓・読取のみ）。

        検証（非 tick ref / 非対応 tf は 400）・生成は純ロジック ``handle_tf_period_profile`` に委譲し、
        本メソッドはクエリ取り出し（``datasetRef``/``timeframe``/``from``/``to``＝ローリング窓）と JSON 応答
        のみを担う（``_handle_market_profile_forming`` と同型の薄殻）。
        """
        ref = (query.get("datasetRef") or [None])[0]
        timeframe = (query.get("timeframe") or [None])[0]
        frm = (query.get("from") or [None])[0]
        to = (query.get("to") or [None])[0]
        src = (query.get("src") or [None])[0]  # 省略時 None＝従来経路（byte 不変）。
        # ISSUE-083 追補: in-memory LiveTickBuffer の末尾を controller へ渡し、当日（未完了セッション）
        #   列を parquet フロンティア遅延（~1分）を待たず最新ティックまで育てる（完了日は controller が
        #   無視＝キャッシュ規約不変）。buffer 未注入・非 tick ref は None＝従来経路（byte 不変）。
        buf = _live_tick_buffer
        live = (buf.ticks_since(0)
                if (buf is not None and forming_bar_mod.is_tick_ref(ref)) else None)
        try:
            status, payload = handle_tf_period_profile(
                ref, timeframe, frm, to, src=src, live_ticks=live)
        except Exception as exc:  # noqa: BLE001（殻の最後の砦・nested で返す）
            self._send_json(
                500, _nested_error("internal", f"tf_period_profile 取得に失敗しました: {exc}"))
            return
        self._send_json(status, payload)

    def _handle_live_ticks(self, query: dict[str, list[str]]) -> None:
        """GET /live_ticks?since=<ms> — バッファの増分 tick を配信する（ISSUE-049・読取のみ）。

        応答 ``{"ok": True, "ticks": [[ms, mid], ...], "serverNowMs": <ms>}``。``since`` より後の
        tick のみ（境界含まず）。buffer 未注入（テスト既定・非 served）は空 ticks を返す。
        フロント（LiveTickPlayer）は serverNowMs で clockOffset を維持し、固定遅延で再生する。
        """
        since_raw = (query.get("since") or ["0"])[0]
        since = int(since_raw) if since_raw.lstrip("-").isdigit() else 0
        buffer = _live_tick_buffer
        ticks = buffer.ticks_since(since) if buffer is not None else []
        self._send_json(
            200, {"ok": True, "ticks": ticks, "serverNowMs": int(time.time() * 1000)}
        )

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

    ISSUE-155: ``ThreadingHTTPServer`` で軽量応答（静的 JS・/candles・/live_ticks 等）を
    並行化し、重い計算（/compute・market_profile 系）は ``_ComputeWorker``（専用スレッド
    1 本）へ直列送致する。fitter="tgp" の rpy2/R はスレッド非安全だが、常に同一ワーカー
    スレッドから呼ばれるため旧・単一スレッド実装と同じ安全性を保つ。これにより重い計算の
    背後で静的配信までもが待たされてページ起動が遅延/ハングする問題を構造的に解消する。
    """
    httpd = ThreadingHTTPServer((host, port), IndicatorUIRequestHandler)
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"
    sys.stdout.write(f"インジケーター管理 UI（B方式）を起動しました: {url}\n")
    sys.stdout.write("  POST /compute  GET /candles?datasetRef=sample  GET /（web/ 静的配信）\n")
    sys.stdout.write("  停止: Ctrl-C\n")
    sys.stdout.flush()

    # ライブ tick バッファ（ISSUE-049・配信系）を起動する。5 秒周期の増分ポーリングを
    #   background thread で回し、/live_ticks へ直近 30 分の tick を供給する。記録系
    #   （parquet/M1/rollups）とは完全分離（メモリのみ・ファイル書込なし）。起動失敗しても
    #   本体配信は継続する（ライブ再生が無効になるだけ・既存 endpoint は不変）。
    try:
        from adapter.compute.live_tick_buffer import LiveTickBuffer

        buffer = LiveTickBuffer()
        set_live_tick_buffer(buffer)
        buffer.start()
    except Exception as exc:  # noqa: BLE001（配信の付加機能・本体起動を妨げない）
        sys.stderr.write(f"  WARN: live tick buffer を起動できませんでした: {exc}\n")
        sys.stderr.flush()
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
