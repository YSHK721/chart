"""serve_sim_jobs — ジョブ API を足した sim コアの HTTP フレームワーク層（Phase 2・F-3）。

Phase 1 の `serve_sim` を**継承で拡張**する（OCP）。`serve_sim` は 1 バイトも変えない。

    SimApp   ← SimJobApp     （配信面 ＋ ジョブ結線）
    Handler  ← Handler       （ジョブ経路のみ横取りし、それ以外は super() へ委譲）

LSP: ジョブ経路（`/jobs*` `/data/*`）以外の GET は `super().do_GET()` へそのまま委譲する。
静的配信の解決・許可根・応答 byte・パストラバーサル防御（CWE-22）は Phase 1 と不変であり、
既存の `test_serve_sim.py` が固定している挙動をそのまま満たす。防御は
`simulator.replay_ui.framework.static_file_server.StaticFileServer` の**単一ソース**の
ままで、ここには写さない（§11.4 複製禁止）。

エンドポイント（§6.1・sim core は prefix 除去後のパスを受ける）:
    POST /jobs                    ジョブ投入
    GET  /jobs/{job_id}           状態照会
    POST /jobs/{job_id}/cancel    取消（FR-12）
    GET  /data/{job_id}/{file}    完了ジョブの結果（静的取得）
ジョブ一覧 GET は作らない（YAGNI 判定済み・§11.4）。

`/data/*` の配信も `StaticFileServer` を**もう 1 インスタンス**（根＝台帳の data_root）
立てて委譲する。パストラバーサル防御を書き直さないため（同じ守りを 2 度書けば必ず食い違う）。
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from simulator.replay_ui.framework.static_file_server import StaticFileServer
from simulator.sim_ui.adapter.job_api_controller import ApiResponse, JobApiController
from simulator.sim_ui.framework.serve_sim import SimApp, make_handler as make_base_handler
from simulator.sim_ui.usecase.cancel_job import CancelJobInteractor
from simulator.sim_ui.usecase.fetch_job_result import FetchJobResultInteractor
from simulator.sim_ui.usecase.query_job import QueryJobInteractor
from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor

# 投入本文の上限（DoS 抑止・ジョブ仕様は小さい JSON）。
_MAX_BODY = 1 << 20
# 接続してから要求行・ヘッダを送り切るまでの待ち上限。設定しないと、接続だけして
# 何も送らないクライアントが ThreadingHTTPServer のワーカースレッドを無期限に占有する。
_HANDLER_TIMEOUT_SEC = 30.0


def is_same_origin_request(headers, host_header: "str | None") -> bool:
    """変更系要求の要求元が自分自身かを判定する（🔴-A・CSRF 遮断）。

    背景（実測された壊れ方）: sim core は loopback 限定でバインドしているが、
    **利用者のブラウザは loopback に到達できる**。悪意あるページが
    ``fetch('http://127.0.0.1:8000/sim/jobs', {method:'POST', mode:'no-cors'})`` を出すと、
    ``Content-Type: text/plain`` なら preflight なしの「単純リクエスト」として実際に
    送信され、ジョブ投入・取消を第三者サイトから起動できる。

    判定順（順序が重要）:
        1. ``Sec-Fetch-Site: same-origin``  → 許可（統合 UI 自身からの fetch）
        2. ``Sec-Fetch-Site`` がそれ以外     → 拒否（cross-site / same-site / none）
        3. ``Origin`` 不在                   → 許可（curl 等の非ブラウザ。ブラウザは
           cross-site なら必ず付ける）
        4. ``Origin`` があり Host と不一致    → 拒否（Sec-Fetch-Site 非対応の旧ブラウザ経路）

    2 を 4 より先に見るのは、**正規 UI の Origin が公開ルータ（:8000）である一方、
    sim core が見る Host は内部ポート（:8381）**だからである（router は Host を
    hop-by-hop として付け替える）。Origin/Host 比較だけで判定すると正規要求まで
    403 になる。`Sec-Fetch-Site` は現行ブラウザが必ず送るため、正規経路はここで通る。

    router が origin / sec-fetch-site を上流へ転送することは確認済み
    （`unified_ui/router.py:122-134` の ``_HOP_BY_HOP`` に含まれない）。
    """
    fetch_site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site:
        return fetch_site == "same-origin"
    origin = (headers.get("Origin") or "").strip()
    if not origin:
        return True          # 非ブラウザ（curl 等）
    if not host_header:
        return False         # Host が無いのに Origin がある＝判定不能。保守側で拒否。
    # Origin は scheme://host[:port]。Host ヘッダは host[:port]。netloc 部で比べる。
    origin_netloc = origin.split("://", 1)[-1].strip().lower()
    return origin_netloc == host_header.strip().lower()


class SimJobApp(SimApp):
    """`SimApp`（配信面）へジョブ実行系を足したアプリケーション面。

    Port 実装（台帳・起動器・系列カタログ）と、必要系列を決める関数は合成根が注入する。
    """

    def __init__(
        self,
        *,
        web_dir: Any = None,
        shared_js_root: Any = None,
        ledger: Any,
        launcher: Any,
        series_catalog: Any,
        required_series: "Callable[[str], str]",
        stop_loss_catalog: Any,
        allowed_backtest_keys: Any,
        required_backtest_keys: Any,
    ) -> None:
        super().__init__(web_dir=web_dir, shared_js_root=shared_js_root)
        self.ledger = ledger
        self.launcher = launcher

        query = QueryJobInteractor(ledger=ledger, launcher=launcher)
        self.controller = JobApiController(
            submit=SubmitJobInteractor(
                ledger=ledger,
                launcher=launcher,
                series_catalog=series_catalog,
                required_series=required_series,
                stop_loss_catalog=stop_loss_catalog,
                allowed_backtest_keys=allowed_backtest_keys,
                required_backtest_keys=required_backtest_keys,
            ),
            query=query,
            cancel=CancelJobInteractor(ledger=ledger, launcher=launcher),
            fetch_result=FetchJobResultInteractor(ledger=ledger, query=query),
        )
        # 結果ペイロードの配信根。防御は StaticFileServer の単一ソースへ委譲する。
        data_root = Path(getattr(ledger, "data_root", ".")).resolve()
        data_root.mkdir(parents=True, exist_ok=True)
        self.result_server = StaticFileServer(data_root, None)


def make_handler(app: SimJobApp):
    """`serve_sim.make_handler` が返すクラスを継承した Handler を返す。"""

    Base = make_base_handler(app)

    class JobHandler(Base):  # type: ignore[valid-type, misc]
        # BaseHTTPRequestHandler は本値を socket のタイムアウトに使う。未設定だと、
        # 接続だけして何も送らないクライアントがワーカースレッドを無期限に占有する。
        timeout = _HANDLER_TIMEOUT_SEC

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path.startswith("/jobs"):
                return self._get_job(path)
            if path.startswith("/data/"):
                return self._get_result(path)
            # ジョブ経路以外は Phase 1 の挙動をそのまま使う（LSP）。
            return super().do_GET()

        def do_POST(self):  # noqa: N802
            # 変更系のみ要求元を検査する（GET＝状態照会は 1 秒間隔ポーリングで
            # 状態を変えないため対象外）。
            if not is_same_origin_request(self.headers, self.headers.get("Host")):
                return self._write(
                    ApiResponse(403, {"error": "要求元が許可されていません"})
                )
            path = urlparse(self.path).path
            if path == "/jobs":
                body = self._read_body()
                if body is None:
                    return None   # 既に 413/400 を書き終えている
                return self._write(app.controller.submit(body))
            segments = _segments(path)
            if len(segments) == 3 and segments[0] == "jobs" and segments[2] == "cancel":
                return self._write(app.controller.cancel(segments[1]))
            return self._write(ApiResponse(404, {"error": "not found"}))

        # --- GET の内訳 --------------------------------------------------

        def _get_job(self, path: str):
            segments = _segments(path)
            # 一覧 GET は作らない（§11.4 YAGNI）。/jobs 単体は 404。
            if len(segments) != 2:
                return self._write(ApiResponse(404, {"error": "not found"}))
            return self._write(app.controller.query(segments[1]))

        def _get_result(self, path: str):
            segments = _segments(path)
            if len(segments) != 3:
                return self._write(ApiResponse(404, {"error": "not found"}))
            _data, job_id, filename = segments
            # 呼ぶのは**公開可否の関門**（完了ジョブか・識別子とファイル名が受理形か）を
            # 通すため。所在の値は使わない——配信は下の StaticFileServer が根から解決する。
            _path, error = app.controller.result_path(job_id, filename)
            if error is not None:
                return self._write(error)
            # 実配信は StaticFileServer（CWE-22 防御つき）へ委譲する。
            return app.result_server.serve(self, f"/{job_id}/{filename}")

        # --- 入出力 ------------------------------------------------------

        def _read_body(self) -> "bytes | None":
            """本文を読む。読めない／読むべきでない場合は応答を返して None。

            上限超過は**本文を読まずに** 413 を返す。`min(length, _MAX_BODY)` で
            切り詰めて読む実装だと、宣言だけ巨大で本文が来ない要求に対して
            `rfile.read` が待ち続け、ワーカースレッドが張り付く（実測: 接続が
            タイムアウトするまで応答が返らない）。宣言値で先に断るのが正しい。
            """
            raw = self.headers.get("Content-Length")
            try:
                length = int(raw or 0)
            except ValueError:
                self._write(ApiResponse(400, {"error": "Content-Length が不正です"}))
                return None
            if length < 0:
                self._write(ApiResponse(400, {"error": "Content-Length が不正です"}))
                return None
            if length > _MAX_BODY:
                self._write(
                    ApiResponse(
                        413,
                        {"error": f"本文が大き過ぎます（上限 {_MAX_BODY} バイト）"},
                    )
                )
                return None
            return self.rfile.read(length) if length > 0 else b""

        def _write(self, response: ApiResponse) -> None:
            body = response.to_bytes()
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return JobHandler


def _segments(path: str) -> "list[str]":
    return [s for s in path.split("/") if s]


def make_server(
    app: SimJobApp, host: str = "127.0.0.1", port: "int | None" = None
) -> ThreadingHTTPServer:
    """サーバを生成して返す（起動はしない）。``port=None`` は空きポート（テスト用）。"""
    return ThreadingHTTPServer((host, port or 0), make_handler(app))


def serve(app: SimJobApp, host: str = "127.0.0.1", port: "int | None" = None) -> None:
    """サーバを起動して待ち受ける（ブロッキング）。"""
    server = make_server(app, host, port)
    actual = server.server_address[1]
    print(f"sim backend: http://{host}:{actual}/  (Ctrl-C 停止)")
    server.serve_forever()
