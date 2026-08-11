"""serve_sim_indicators（指標一覧 API つき sim core・framework）の結合検定。

固定する不変条件（Phase 3 構造設計 §新規ファイル #10-12・§6.1・契約改訂裁定 A/C）:
    1. `GET /indicators` は台帳の**系列**一覧を 200 で返す。不一致・未検定・供給コスト
       超過の系列も `selectable=false` と reason（3 値固定）つきで含む（無音で消さない）。
    2. 台帳が無いときは **503**（fail-closed）。空一覧の 200 に倒さない。空 200 は
       「検定した結果 0 件」と区別がつかず、未検定を「検定済み」と誤読させる。
    3. **既存の静的配信面は 1 バイトも変わらない**（Phase 1 / Phase 2 と同一）。
       パストラバーサル防御（CWE-22）もそのまま効く。
    4. **既存のジョブ API 経路が不変**。`SimIndicatorApp` は `SimJobApp` を継承せず
       委譲で包むため、`app.controller` / `app.result_server` が `__getattr__` 経由で
       解決されることを実 HTTP で実証する（受け口だけ作って結線が死ぬ形＝ISSUE-291 防止）。
    5. `POST /indicators` は作らない（YAGNI）。

方式: 実 HTTP（port=0 の空きポート。固定ポートを掴まない）。既存
`test_serve_sim_jobs.py` と同方式。子プロセスはフェイク起動器へ差し替える。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.file_indicator_causality_ledger import (
    FileIndicatorCausalityLedger,
)
from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.adapter.indicator_api_controller import IndicatorApiController
from simulator.sim_ui.framework.serve_sim_indicators import SimIndicatorApp, make_server
from simulator.sim_ui.framework.serve_sim_jobs import SimJobApp
from simulator.sim_ui.tests.integration._fake_ports import (
    FakeLauncher,
    FakeSeriesCatalog,
    FakeStopLossCatalog,
    allowed_backtest_keys,
    no_required_backtest_keys,
    required_series,
)
from simulator.sim_ui.usecase.indicator_models import (
    REASON_MISMATCH,
    REASON_SUPPLY_COST_EXCEEDED,
    REASON_VERIFICATION_INCOMPLETE,
    CausalityFinding,
    IndicatorSpec,
    LedgerConditions,
    LedgerSnapshot,
)

_CATALOG = FakeSeriesCatalog({"PRO_fit_Band_EA": frozenset({"ema", "close"})})
_CONDITIONS = LedgerConditions(
    ref="jp225_tick", timeframe="5m", supply_bars=10_000, verify_bars=1_000,
    verify_coverage=1.0, timeout=600.0, supply_budget=1.0, limit=None,
    tolerance=0.0, probe_mode="full",
)


def _snapshot() -> LedgerSnapshot:
    """一致 1 系列 + 3 種の選択不可（reason 3 値をすべて含む）。"""
    return LedgerSnapshot(
        schema=1, measured_at="2026-08-11T12:00:00Z", conditions=_CONDITIONS,
        findings=(
            CausalityFinding(
                spec=IndicatorSpec("moving_averages", "default", {"length": 20}),
                series_name="MA", selectable=True, bars_compared=10_000,
                max_abs_diff=0.0, supply_seconds=0.31,
            ),
            CausalityFinding(
                spec=IndicatorSpec("cvfe", "default", {"n_har": 500}),
                series_name="MID", selectable=False, reason=REASON_MISMATCH,
                detail="最初の不一致 time=1755000000 max_abs_diff=1.25",
                bars_compared=10_000, max_abs_diff=1.25,
                first_mismatch_time=1_755_000_000, supply_seconds=0.62,
            ),
            CausalityFinding(
                spec=IndicatorSpec("profit_band", "robust", {}),
                series_name="UPPER", selectable=False,
                reason=REASON_SUPPLY_COST_EXCEEDED,
                detail="供給 73.800 秒 > 予算 1.0 秒（供給窓 10000 本）",
                supply_seconds=73.8,
            ),
            CausalityFinding(
                spec=IndicatorSpec("tgp_btlm", "default", {}),
                series_name="TGP", selectable=False,
                reason=REASON_VERIFICATION_INCOMPLETE,
                detail="検定予算 600.0 秒を超過しました（120/10000 本まで検定）",
                bars_compared=120, supply_seconds=0.9,
            ),
        ),
    )


def _build_app(tmp_path: Path, *, with_ledger: bool):
    web = tmp_path / "sim_web"
    (web / "js").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>sim</title>", encoding="utf-8")
    (web / "js" / "boot.js").write_text("export const ok = 1;\n", encoding="utf-8")
    secret = tmp_path / "sim_web_SECRET"
    secret.mkdir()
    (secret / "leak.txt").write_text("TOP_SECRET", encoding="utf-8")
    (web / "link").symlink_to(secret, target_is_directory=True)

    job_ledger = FileJobLedger(data_root=tmp_path / "data")
    launcher = FakeLauncher()
    inner = SimJobApp(
        web_dir=web, shared_js_root=None, ledger=job_ledger, launcher=launcher,
        series_catalog=_CATALOG, required_series=required_series,
        stop_loss_catalog=FakeStopLossCatalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )
    causality = FileIndicatorCausalityLedger(data_root=tmp_path / "data")
    if with_ledger:
        causality.write(_snapshot())
    app = SimIndicatorApp(
        inner=inner, controller=IndicatorApiController(ledger=causality)
    )
    return app, job_ledger, launcher


def _serve(app):
    srv = make_server(app, "127.0.0.1", None)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{port}"


@pytest.fixture
def server(tmp_path: Path):
    app, ledger, launcher = _build_app(tmp_path, with_ledger=True)
    srv, thread, base = _serve(app)
    try:
        yield base, ledger, launcher
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


@pytest.fixture
def server_without_ledger(tmp_path: Path):
    app, _ledger, _launcher = _build_app(tmp_path, with_ledger=False)
    srv, thread, base = _serve(app)
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


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


# --- 1. 系列一覧（不変条件 1）--------------------------------------------

def test_系列一覧を200で返す(server) -> None:
    base, _l, _p = server
    status, payload = _json(base, "/indicators")
    assert status == 200
    assert payload["ok"] is True
    assert [(i["indicator"], i["series"]) for i in payload["series"]] == [
        ("cvfe", "MID"), ("moving_averages", "MA"),
        ("profit_band", "UPPER"), ("tgp_btlm", "TGP"),
    ]


def test_選択可能な系列が列挙できる(server) -> None:
    """通過条件 1（一致系列の列挙）を応答から機械的に取れる。"""
    base, _l, _p = server
    _status, payload = _json(base, "/indicators")
    assert [i["series"] for i in payload["series"] if i["selectable"]] == ["MA"]


def test_選択不可の系列も理由つきで含まれる(server) -> None:
    """通過条件 2。無音で消さない（利用者に「無い」としか見えなくなる）。"""
    base, _l, _p = server
    _status, payload = _json(base, "/indicators")
    rejected = {i["series"]: i for i in payload["series"] if not i["selectable"]}
    assert set(rejected) == {"MID", "UPPER", "TGP"}
    assert all(i["reason"] for i in rejected.values())
    assert all(i["detail"] for i in rejected.values())


def test_選択不可の理由は3値に固定される(server) -> None:
    """自由文が混ざると機械判定（絞り込み・再検定の要否）が壊れる。"""
    base, _l, _p = server
    _status, payload = _json(base, "/indicators")
    reasons = {i["reason"] for i in payload["series"] if not i["selectable"]}
    assert reasons == {
        "mismatch", "supply_cost_exceeded", "verification_incomplete"
    }


def test_選択可能な系列に理由は付かない(server) -> None:
    base, _l, _p = server
    _status, payload = _json(base, "/indicators")
    assert all(
        i["reason"] is None for i in payload["series"] if i["selectable"]
    )


def test_測定条件が応答に載る(server) -> None:
    base, _l, _p = server
    _status, payload = _json(base, "/indicators")
    assert payload["measured_at"] == "2026-08-11T12:00:00Z"
    assert payload["conditions"] == {
        "ref": "jp225_tick", "timeframe": "5m",
        "supply_bars": 10_000, "verify_bars": 1_000, "verify_coverage": 1.0,
        "timeout": 600.0, "supply_budget": 1.0,
        "limit": None, "tolerance": 0.0, "probe_mode": "full",
    }


def test_paramsが応答に載る(server) -> None:
    base, _l, _p = server
    _status, payload = _json(base, "/indicators")
    ma = [i for i in payload["series"] if i["indicator"] == "moving_averages"][0]
    assert ma["params"] == {"length": 20}


# --- 2. fail-closed（不変条件 2）------------------------------------------

def test_台帳が無ければ503(server_without_ledger) -> None:
    """空一覧の 200 に倒さない。未検定を「検定済み 0 件」と誤読させる。"""
    status, payload = _json(server_without_ledger, "/indicators")
    assert status == 503
    assert payload["error"]


# --- 3. 既存の静的配信面は不変（不変条件 3）------------------------------

def test_静的配信は従来どおり(server) -> None:
    base, _l, _p = server
    status, body = _request(base, "/")
    assert status == 200
    assert b"<!doctype html>" in body.lower()


def test_静的JSも従来どおり(server) -> None:
    base, _l, _p = server
    status, body = _request(base, "/js/boot.js")
    assert status == 200
    assert b"export const ok" in body


@pytest.mark.parametrize("path", [
    "/../sim_web_SECRET/leak.txt",
    "/js/../../sim_web_SECRET/leak.txt",
    "/link/leak.txt",
])
def test_パストラバーサル防御は委譲後も効く(server, path: str) -> None:
    """CWE-22。JSON ルート層を挟んでも静的側の単一ソース防御が働く。"""
    base, _l, _p = server
    status, body = _request(base, path)
    assert b"TOP_SECRET" not in body
    assert status == 404


def test_不在パスは404(server) -> None:
    base, _l, _p = server
    status, _ = _request(base, "/does-not-exist.js")
    assert status == 404


def test_indicatorsに似た別パスは静的側へ落ちる(server) -> None:
    """prefix の境界を跨いで JSON 応答を返さない。"""
    base, _l, _p = server
    status, _ = _request(base, "/indicators-extra.js")
    assert status == 404


# --- 4. 既存ジョブ API の不変（不変条件 4）--------------------------------

def _submit(base):
    return _json(
        base, "/jobs", method="POST",
        body={"backtest": {"ea_name": "PRO_fit_Band_EA", "symbol": "JP225"}},
    )


def test_ジョブ投入は委譲後も動く(server) -> None:
    """`app.controller` が `__getattr__` 経由で解決されること。"""
    base, _l, launcher = server
    status, got = _submit(base)
    assert status == 202
    assert got["job_id"]
    assert launcher.launched == [got["job_id"]]


def test_状態照会は委譲後も動く(server) -> None:
    base, _l, _p = server
    _s, submitted = _submit(base)
    status, got = _json(base, f"/jobs/{submitted['job_id']}")
    assert status == 200
    assert got["status"] == "running"


def test_取消は委譲後も動く(server) -> None:
    base, _l, _p = server
    _s, job = _submit(base)
    status, got = _json(base, f"/jobs/{job['job_id']}/cancel", method="POST")
    assert status == 200
    assert got["status"] == "cancelled"


def test_結果配信は委譲後も動く(server) -> None:
    """`app.result_server`（`/data/*` の StaticFileServer）も委譲で解決される。"""
    base, ledger, launcher = server
    _s, job = _submit(base)
    (ledger.job_dir(job["job_id"]) / "stats.json").write_text(
        '{"trades": 3}', encoding="utf-8"
    )
    launcher.finish(job["job_id"], 0)
    _json(base, f"/jobs/{job['job_id']}")  # 照合を進める
    status, body = _request(base, f"/data/{job['job_id']}/stats.json")
    assert status == 200
    assert json.loads(body)["trades"] == 3


def test_ジョブ一覧GETは存在しないまま(server) -> None:
    base, _l, _p = server
    status, _ = _request(base, "/jobs")
    assert status == 404


def test_内側のアプリ属性が委譲で解決される(server, tmp_path: Path) -> None:
    """framework の Handler は `app.controller` / `app.result_server` を属性で引く。"""
    app, _ledger, _launcher = _build_app(tmp_path / "x", with_ledger=True)
    assert app.controller is not None
    assert app.result_server is not None
    assert app.web_dir is not None


# --- 5. YAGNI（不変条件 5）------------------------------------------------

def test_指標一覧のPOSTは作らない(server) -> None:
    base, _l, _p = server
    status, _ = _request(base, "/indicators", method="POST", body={})
    assert status == 404
