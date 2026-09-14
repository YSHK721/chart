"""serve_sim_jobs（ジョブ API つき sim core・framework）の結合検定。

固定する不変条件（§6.1・§12.7）:
    1. API（sim core は prefix 除去後のパスを受ける）
         POST /jobs            投入 → job_id
         GET  /jobs/{id}       状態照会（未知は 404）
         POST /jobs/{id}/cancel 取消（終端からの取消は 409）
         GET  /data/{id}/stats.json 完了ジョブの結果（完了前は公開しない）
       ジョブ一覧 GET は**作らない**（YAGNI 判定済み・§11.4）。
    2. **静的配信の挙動は Phase 1 と変わらない**（LSP）。`serve_sim.make_handler` が
       返すクラスを継承し、ジョブ経路以外は `super().do_GET()` へ委譲する。
       パストラバーサル防御（CWE-22）もそのまま効く。
    3. **並列実行**（§12.7）: 2 ジョブを同時に投入して両方が実行中になる。
       一方の取消が他方に波及しない。
    4. E-3（§12.5）: 価格系列を持たない戦略への sizing ON は **400** で拒否。
    5. 部分結果の非公開（§12.7 fail-stop）: 未完了・取消・失敗のジョブの結果は出さない。

方式: 実 HTTP（port=0 の空きポート。固定ポートを掴まない）。子プロセスは
      フェイク起動器へ差し替え、HTTP 層と結線だけを対象にする。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.framework.serve_sim_jobs import SimJobApp, make_server
from simulator.sim_ui.domain.simulation_job import JobStatus
from simulator.sim_ui.tests.integration._fake_ports import (
    FakeLauncher,
    FakeSeriesCatalog,
    FakeStopLossCatalog,
    allowed_backtest_keys,
    no_required_backtest_keys,
    required_series,
    submission,
)

_CATALOG = FakeSeriesCatalog(
    {
        "MA_Slope_EA": frozenset({"ema"}),
        "PRO_fit_Band_EA": frozenset({"ema", "close"}),
        "WeeklyVolBand_EA": frozenset({"open"}),
    }
)


# 空カタログ＝「SL は設定パラメータで決まらない」＝受付では判定しない。
_NO_STOP_LOSS_PARAMS = FakeStopLossCatalog()


@pytest.fixture
def server(tmp_path: Path):
    """実 HTTP の sim core（空きポート）とフェイク起動器を返す。"""
    web = tmp_path / "sim_web"
    (web / "js").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>sim</title>", encoding="utf-8")
    (web / "js" / "boot.js").write_text("export const ok = 1;\n", encoding="utf-8")
    secret = tmp_path / "sim_web_SECRET"
    secret.mkdir()
    (secret / "leak.txt").write_text("TOP_SECRET", encoding="utf-8")
    (web / "link").symlink_to(secret, target_is_directory=True)

    ledger = FileJobLedger(data_root=tmp_path / "data")
    launcher = FakeLauncher()
    app = SimJobApp(
        web_dir=web,
        shared_js_root=None,
        ledger=ledger,
        launcher=launcher,
        series_catalog=_CATALOG,
        required_series=required_series,
        # §12.8 の SL 受付検証は本ファイルの検証対象外なので、SL 系設定パラメータを
        # 「持たない」カタログ（＝受付では判定しない）を注入し既存の検証内容を保つ。
        stop_loss_catalog=_NO_STOP_LOSS_PARAMS,
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )
    srv = make_server(app, "127.0.0.1", None)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", ledger, launcher
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


def _request(base, path, *, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(base, path, *, method="GET", body=None):
    status, raw = _request(base, path, method=method, body=body)
    return status, (json.loads(raw) if raw else None)


def _submit(base, ea_name="PRO_fit_Band_EA", sizing=None):
    return _json(
        base, "/jobs", method="POST",
        body={"backtest": {"ea_name": ea_name, "symbol": "JP225"}, "sizing": sizing},
    )


# --- 1. 静的配信は Phase 1 のまま（LSP）-----------------------------------

def test_静的配信は従来どおり(server) -> None:
    base, _, _ = server
    status, body = _request(base, "/")
    assert status == 200
    assert b"<!doctype html>" in body.lower()


def test_静的JSも従来どおり(server) -> None:
    base, _, _ = server
    status, body = _request(base, "/js/boot.js")
    assert status == 200
    assert b"export const ok" in body


@pytest.mark.parametrize("path", [
    "/../sim_web_SECRET/leak.txt",
    "/js/../../sim_web_SECRET/leak.txt",
    "/link/leak.txt",
])
def test_パストラバーサル防御は継承後も効く(server, path: str) -> None:
    """CWE-22。継承で壊していないこと（防御は StaticFileServer の単一ソース）。"""
    base, _, _ = server
    status, body = _request(base, path)
    assert b"TOP_SECRET" not in body
    assert status == 404


def test_不在パスは404(server) -> None:
    base, _, _ = server
    status, _ = _request(base, "/does-not-exist.js")
    assert status == 404


# --- 2. 投入 ---------------------------------------------------------------

def test_投入するとjob_idが返る(server) -> None:
    base, _, _ = server
    status, got = _submit(base)
    assert status in (200, 202)
    assert got["job_id"]
    assert got["status"] == "running"


def test_投入で子プロセスが起動する(server) -> None:
    base, _, launcher = server
    _status, got = _submit(base)
    assert launcher.launched == [got["job_id"]]


def test_投入本文が不正なら400(server) -> None:
    base, _, _ = server
    status, _ = _request(base, "/jobs", method="POST", body={"no_backtest": 1})
    assert status == 400


def test_ジョブ一覧GETは存在しない(server) -> None:
    """YAGNI 判定済み（§11.4）。作っていないことを固定する。"""
    base, _, _ = server
    status, _ = _request(base, "/jobs")
    assert status == 404


# --- 3. 状態照会 -----------------------------------------------------------

def test_状態を照会できる(server) -> None:
    base, _, _ = server
    _s, submitted = _submit(base)
    status, got = _json(base, f"/jobs/{submitted['job_id']}")
    assert status == 200
    assert got["job_id"] == submitted["job_id"]
    assert got["status"] == "running"
    assert got["failure_reason"] is None


def test_未知のジョブ照会は404(server) -> None:
    base, _, _ = server
    status, _ = _json(base, "/jobs/" + "f" * 32)
    assert status == 404


def test_子プロセス完了後の照会は完了になる(server) -> None:
    base, _, launcher = server
    _s, submitted = _submit(base)
    launcher.finish(submitted["job_id"], 0)
    status, got = _json(base, f"/jobs/{submitted['job_id']}")
    assert status == 200
    assert got["status"] == "completed"


def test_子プロセス異常終了後の照会は失敗になる(server) -> None:
    base, _, launcher = server
    _s, submitted = _submit(base)
    launcher.finish(submitted["job_id"], 1)
    _status, got = _json(base, f"/jobs/{submitted['job_id']}")
    assert got["status"] == "failed"
    assert "1" in got["failure_reason"]


# --- 4. 並列実行（§12.7）--------------------------------------------------

def test_2ジョブを同時に投入して両方実行中になる(server) -> None:
    base, _, launcher = server
    _s1, a = _submit(base)
    _s2, b = _submit(base, "WeeklyVolBand_EA")
    assert a["job_id"] != b["job_id"]
    assert launcher.launched == [a["job_id"], b["job_id"]]
    assert _json(base, f"/jobs/{a['job_id']}")[1]["status"] == "running"
    assert _json(base, f"/jobs/{b['job_id']}")[1]["status"] == "running"


def test_一方の取消は他方に波及しない(server) -> None:
    base, _, _ = server
    _s1, a = _submit(base)
    _s2, b = _submit(base)
    _json(base, f"/jobs/{a['job_id']}/cancel", method="POST")
    assert _json(base, f"/jobs/{a['job_id']}")[1]["status"] == "cancelled"
    assert _json(base, f"/jobs/{b['job_id']}")[1]["status"] == "running"


# --- 5. 取消 ---------------------------------------------------------------

def test_取消できる(server) -> None:
    base, _, launcher = server
    _s, job = _submit(base)
    status, got = _json(base, f"/jobs/{job['job_id']}/cancel", method="POST")
    assert status == 200
    assert got["status"] == "cancelled"
    assert launcher.terminated == [job["job_id"]]


def test_二重取消は409(server) -> None:
    """§12.7 終端確定。終端からの遷移は不変条件違反。"""
    base, _, _ = server
    _s, job = _submit(base)
    _json(base, f"/jobs/{job['job_id']}/cancel", method="POST")
    status, _ = _json(base, f"/jobs/{job['job_id']}/cancel", method="POST")
    assert status == 409


def test_未知のジョブ取消は404(server) -> None:
    base, _, _ = server
    status, _ = _json(base, "/jobs/" + "e" * 32 + "/cancel", method="POST")
    assert status == 404


# --- 6. E-3（§12.5）------------------------------------------------------

def test_価格系列を持たない戦略のsizingONは400で拒否される(server) -> None:
    base, _, launcher = server
    status, got = _submit(base, "MA_Slope_EA", sizing={"enabled": True})
    assert status == 400
    assert "MA_Slope_EA" in json.dumps(got, ensure_ascii=False)
    assert launcher.launched == [], "拒否したのに子プロセスを起こしている"


def test_sizingOFFなら同じ戦略でも投入できる(server) -> None:
    base, _, _ = server
    status, _got = _submit(base, "MA_Slope_EA", sizing={"enabled": False})
    assert status in (200, 202)


# --- 7. 結果取得（部分結果の非公開・§12.7）--------------------------------

def _write_result(ledger: FileJobLedger, job_id: str) -> None:
    (ledger.job_dir(job_id) / "stats.json").write_text('{"trades": 3}', encoding="utf-8")


def test_完了ジョブの結果を取得できる(server) -> None:
    base, ledger, launcher = server
    _s, job = _submit(base)
    _write_result(ledger, job["job_id"])
    launcher.finish(job["job_id"], 0)
    _json(base, f"/jobs/{job['job_id']}")  # 照合を進める
    status, body = _request(base, f"/data/{job['job_id']}/stats.json")
    assert status == 200
    assert json.loads(body)["trades"] == 3


def test_実行中のジョブの結果は公開されない(server) -> None:
    """途中まで書かれた stats.json を掴ませない。"""
    base, ledger, _ = server
    _s, job = _submit(base)
    _write_result(ledger, job["job_id"])
    status, body = _request(base, f"/data/{job['job_id']}/stats.json")
    assert status != 200
    assert b"trades" not in body


def test_取消したジョブの部分結果は公開されない(server) -> None:
    """§12.7 fail-stop。取消後に完了へ化けて結果が出ることはない。"""
    base, ledger, launcher = server
    _s, job = _submit(base)
    _write_result(ledger, job["job_id"])
    _json(base, f"/jobs/{job['job_id']}/cancel", method="POST")
    launcher.finish(job["job_id"], 0)
    status, body = _request(base, f"/data/{job['job_id']}/stats.json")
    assert status != 200
    assert b"trades" not in body


def test_失敗したジョブの部分結果は公開されない(server) -> None:
    base, ledger, launcher = server
    _s, job = _submit(base)
    _write_result(ledger, job["job_id"])
    launcher.finish(job["job_id"], 1)
    _json(base, f"/jobs/{job['job_id']}")
    status, body = _request(base, f"/data/{job['job_id']}/stats.json")
    assert status != 200
    assert b"trades" not in body


def test_結果パスで外へ出られない(server) -> None:
    """CWE-22。/data 経由でジョブディレクトリの外を読ませない。"""
    base, _, _ = server
    status, body = _request(base, "/data/" + "a" * 32 + "/../../../etc/passwd")
    assert status != 200
    assert b"root:" not in body


# --- HTTP 面の防御（コードレビュー 🟡-2）----------------------------------

def test_巨大なContent_Lengthは本文を読まずに413(server) -> None:
    """未読の巨大本文を読み込むとメモリを食う。宣言値で先に断る。"""
    import http.client

    base, _ledger, _launcher = server
    host, port = base.split("//", 1)[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    # Arrange: 本文は送らず Content-Length だけ巨大に宣言する
    conn.putrequest("POST", "/jobs")
    conn.putheader("Content-Length", str(64 * 1024 * 1024))
    conn.endheaders()
    # Act
    resp = conn.getresponse()
    # Assert
    assert resp.status == 413
    conn.close()


def test_Content_Lengthが不正なら400(server) -> None:
    import http.client

    base, _ledger, _launcher = server
    host, port = base.split("//", 1)[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.putrequest("POST", "/jobs")
    conn.putheader("Content-Length", "not-a-number")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


def test_ハンドラにタイムアウトが設定されている() -> None:
    """接続したまま何も送らないクライアントでワーカースレッドを占有させない。"""
    from simulator.sim_ui.framework.serve_sim_jobs import make_handler

    handler = make_handler(object())
    assert getattr(handler, "timeout", None), "Handler に timeout が無い"


# --- 変更系エンドポイントの要求元検査（コードレビュー 🔴-A）----------------

# 実測された壊れ方: sim core は loopback 限定でバインドしているが、**利用者のブラウザは
# loopback に到達できる**。悪意あるページが `fetch('http://127.0.0.1:8000/sim/jobs',
# {method:'POST', mode:'no-cors'})` を出すと、`Content-Type: text/plain` なら
# preflight なしの「単純リクエスト」として実際に送信され、ジョブ投入・取消が
# 第三者サイトから起動できる（CSRF）。GET は状態を変えないので対象外。
#
# 判定規則（router は origin / sec-fetch-site を上流へ転送する＝`_HOP_BY_HOP` 非該当・
# `unified_ui/router.py:122-134` で確認済み。よって検査点は sim core で機能する）:
#   1. `Sec-Fetch-Site: same-origin`   → 許可（統合 UI 自身からの fetch）
#   2. `Sec-Fetch-Site` がそれ以外      → 403（cross-site 等）
#   3. `Origin` 不在                    → 許可（curl 等の非ブラウザ。ブラウザは必ず付ける）
#   4. `Origin` があり Host と不一致     → 403
# 順序が重要: 正規 UI の Origin は公開ルータ（:8000）だが sim core が見る Host は
# 内部ポート（:8381）なので、Origin/Host 比較だけでは正規要求まで落ちる。
# `Sec-Fetch-Site` を先に見ることで正規経路を通し、旧ブラウザ経路のみ Origin 比較へ落とす。

_VALID_SUBMIT_BODY = json.dumps(
    {"backtest": {"ea_name": "PRO_fit_Band_EA"}, "sizing": None}
).encode()


def _request_with_headers(base, path, *, method, headers, body=_VALID_SUBMIT_BODY):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, data=body, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_他サイト由来のジョブ投入は403(server) -> None:
    """CSRF の実形。単純リクエスト（text/plain）で preflight を回避してくる。"""
    base, _ledger, _launcher = server
    status = _request_with_headers(
        base, "/jobs", method="POST",
        headers={
            "Origin": "https://evil.example",
            "Content-Type": "text/plain",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert status == 403, f"他サイトからのジョブ投入が通った（status={status}）"


def test_他サイト由来の取消は403(server) -> None:
    base, ledger, launcher = server
    job = ledger.create(submission())
    ledger.update(job.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    status = _request_with_headers(
        base, f"/jobs/{job.job_id}/cancel", method="POST",
        headers={
            "Origin": "https://evil.example",
            "Content-Type": "text/plain",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert status == 403, f"他サイトからの取消が通った（status={status}）"


def test_Sec_Fetch_Siteが無くてもOriginが不一致なら403(server) -> None:
    """`Sec-Fetch-Site` を送らない古いブラウザ経路も塞ぐ。"""
    base, _ledger, _launcher = server
    status = _request_with_headers(
        base, "/jobs", method="POST",
        headers={"Origin": "https://evil.example", "Content-Type": "text/plain"},
    )
    assert status == 403


# --- 許可側（将来の正規 UI 投入を塞がないこと）-----------------------------

def test_統合UI自身からの投入は受理される(server) -> None:
    """正規 UI の fetch は `Sec-Fetch-Site: same-origin` を伴う。

    正規 UI の Origin は公開ルータ（:8000）だが sim core が見る Host は内部ポートなので、
    Origin/Host 比較だけで判定すると**正規要求まで 403 になる**。ここが通ることを固定する。
    """
    base, _ledger, _launcher = server
    status = _request_with_headers(
        base, "/jobs", method="POST",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        },
        body=json.dumps(
            {"backtest": {"ea_name": "PRO_fit_Band_EA"}, "sizing": None}
        ).encode(),
    )
    assert status in (200, 202), f"正規 UI からの投入が塞がれた（status={status}）"


def test_Originを付けない要求は受理される(server) -> None:
    """curl 等の非ブラウザ。ブラウザは cross-site なら必ず Origin を付ける。"""
    base, _ledger, _launcher = server
    status = _request_with_headers(
        base, "/jobs", method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {"backtest": {"ea_name": "PRO_fit_Band_EA"}, "sizing": None}
        ).encode(),
    )
    assert status in (200, 202), f"curl 型の投入が塞がれた（status={status}）"


def test_GETは要求元検査の対象外(server) -> None:
    """状態を変えない読み取りは塞がない（§6.1 の状態照会は 1 秒間隔ポーリング）。"""
    base, ledger, _launcher = server
    job = ledger.create(submission())
    status = _request_with_headers(
        base, f"/jobs/{job.job_id}", method="GET",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        body=None,
    )
    assert status == 200


# --- 受付検証エラーの HTTP 変換（🟡-A / 🔵-C の結線漏れ）------------------

# 実測された壊れ方: `JobSubmissionInvalidError` を新設したのに controller が
# `SizingUnsupportedError` しか捕捉しておらず、未知キー・必須欠落の投入で例外が
# ハンドラまで抜けて **接続が切れる**（`http.client.RemoteDisconnected`）。
# 利用者からは「サーバが落ちた」に見え、何が悪いのか一切分からない。

def test_必須キー欠落の投入は400で理由が返る(server) -> None:
    base, _ledger, _launcher = server
    status, payload = _json(
        base, "/jobs", method="POST",
        body={"backtest": {"ea_name": "PRO_fit_Band_EA"}, "sizing": None},
    )
    # 本 fixture は必須検査を課さないので受理される（対照）。
    assert status == 202


def test_未知キーの投入は400で理由が返る(tmp_path: Path) -> None:
    """接続断ではなく、理由の載った 400 を返すこと。"""
    import threading
    import urllib.error
    import urllib.request

    from simulator.sim_ui.framework.serve_sim_jobs import SimJobApp, make_server

    # Arrange（許可集合・必須集合とも本番同等を注入する）
    web = tmp_path / "web"
    web.mkdir()
    app = SimJobApp(
        web_dir=web,
        shared_js_root=None,
        ledger=FileJobLedger(data_root=tmp_path / "data"),
        launcher=FakeLauncher(),
        series_catalog=_CATALOG,
        required_series=required_series,
        stop_loss_catalog=_NO_STOP_LOSS_PARAMS,
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )
    srv = make_server(app, "127.0.0.1", None)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/jobs",
            data=json.dumps(
                {"backtest": {"ea_name": "PRO_fit_Band_EA", "bogus": 1}}
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        # Act
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        # Assert
        assert status == 400, f"接続断や 5xx ではなく 400 を返すこと（status={status}）"
        assert "bogus" in json.loads(raw)["error"]
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)
