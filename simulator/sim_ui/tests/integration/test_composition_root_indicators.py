"""composition_root_indicators（指標一覧つき sim core の DI 結線・main 層）の結合検定。

固定する不変条件（Phase 3 構造設計 §新規ファイル #13）:
    1. Phase 2 の `build_sim_job_app` を**包む**（結線を複製しない）。配信面・台帳・
       起動器の規約は Phase 2 と同一（既定 data_root・shared_js_root の出所を 1 つに保つ）。
    2. 因果性台帳の根は**ジョブ台帳と同じ根**（`data_root` の既定値を二重定義しない）。
    3. 実物の Port 実装が結線される（フェイクが本番へ漏れない）。
    4. 端から端まで（合成根 → framework → 実 HTTP）1 本で通る。受け口だけ作って
       呼び出し側が結線されない形（ISSUE-291）を作らない。
    5. 台帳が未生成の状態でも起動でき、`/indicators` は 503 を返す（fail-closed）。
       起動時の自動再検定はしない（YAGNI・検定は CLI の責務）。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from simulator.sim_ui.adapter.file_indicator_causality_ledger import (
    FileIndicatorCausalityLedger,
)
from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.adapter.subprocess_job_launcher import SubprocessJobLauncher
from simulator.sim_ui.framework.serve_sim_indicators import SimIndicatorApp, make_server
from simulator.sim_ui.framework.serve_sim_jobs import SimJobApp
from simulator.sim_ui.main.composition_root_indicators import build_sim_indicator_app
from simulator.sim_ui.usecase.indicator_models import (
    CausalityFinding,
    IndicatorSpec,
    LedgerConditions,
    LedgerSnapshot,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]


# --- 1. 組み上がり（不変条件 1・3）----------------------------------------

def test_指標一覧つきのアプリが組み上がる(tmp_path: Path) -> None:
    # Arrange / Act
    app = build_sim_indicator_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert isinstance(app, SimIndicatorApp)


def test_内側はPhase2のアプリ(tmp_path: Path) -> None:
    """結線を複製せず `build_sim_job_app` を包む。"""
    # Arrange / Act
    app = build_sim_indicator_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert isinstance(app.inner, SimJobApp)
    assert isinstance(app.ledger, FileJobLedger)
    assert isinstance(app.launcher, SubprocessJobLauncher)


def test_配信面の規約はPhase2と同じ(tmp_path: Path) -> None:
    # Arrange / Act
    app = build_sim_indicator_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert app.shared_js_root == (
        tmp_path / "indigators" / "indicator_ui" / "web"
    ).resolve()
    assert app.web_dir == (tmp_path / "web").resolve()


# --- 2. 台帳の根（不変条件 2）--------------------------------------------

def test_因果性台帳の根はジョブ台帳と同じ(tmp_path: Path) -> None:
    """既定値を二重定義しない（片方だけ動いて片方が置き去りになる形を作らない）。"""
    # Arrange / Act
    app = build_sim_indicator_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert isinstance(app.causality_ledger, FileIndicatorCausalityLedger)
    assert app.causality_ledger.path.parent == app.ledger.data_root


def test_台帳の根は明示指定に追随する(tmp_path: Path) -> None:
    # Arrange / Act
    app = build_sim_indicator_app(
        repo_root=tmp_path, web_dir=tmp_path / "web", data_root=tmp_path / "jobs"
    )
    # Assert
    assert app.causality_ledger.path == (
        tmp_path / "jobs" / "indicator_causality.json"
    ).resolve()


def test_台帳の根は既定でsim_uiのdata配下(tmp_path: Path) -> None:
    # Arrange / Act
    app = build_sim_indicator_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert app.causality_ledger.path == (
        tmp_path / "simulator" / "sim_ui" / "data" / "indicator_causality.json"
    ).resolve()


# --- 3. 端から端まで（不変条件 4・5）--------------------------------------

def _minimal_backtest() -> dict:
    from simulator.sim_ui.main.composition_root_jobs import required_backtest_keys

    backtest = {name: 0 for name in required_backtest_keys()}
    backtest["ea_name"] = "PRO_fit_Band_EA"
    return backtest


def _get(base: str, path: str):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_合成根の結線で実HTTPが応答する(tmp_path: Path) -> None:
    """合成根 → framework → 実 HTTP を 1 本で通す（固定ポートは掴まない）。"""
    # Arrange（台帳を 1 件だけ機械生成した状態）
    web = _REPO_ROOT / "simulator" / "sim_ui" / "web"
    app = build_sim_indicator_app(web_dir=web, data_root=tmp_path / "data")
    app.causality_ledger.write(LedgerSnapshot(
        schema=1, measured_at="2026-08-11T00:00:00Z",
        conditions=LedgerConditions(
            ref="jp225_tick", timeframe="5m", supply_bars=10_000, verify_bars=1_000,
            verify_coverage=1.0, timeout=None, supply_budget=1.0, limit=None,
            tolerance=0.0, probe_mode="full",
        ),
        findings=(CausalityFinding(
            spec=IndicatorSpec("moving_averages", "default", {}),
            series_name="MA", selectable=True, bars_compared=10_000,
            max_abs_diff=0.0, supply_seconds=0.31,
        ),),
    ))
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # Act
        index_status, _ = _get(base, "/")
        indicators_status, indicators_body = _get(base, "/indicators")
        request = urllib.request.Request(
            base + "/jobs",
            data=json.dumps({"backtest": _minimal_backtest()}).encode(),
            method="POST", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            submit_status = response.status
        # Assert
        assert index_status == 200
        assert indicators_status == 200
        assert json.loads(indicators_body)["series"][0]["selectable"] is True
        assert submit_status == 202
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_台帳未生成でも起動でき指標一覧は503(tmp_path: Path) -> None:
    """起動時の自動再検定はしない（YAGNI）。検定は CLI の責務。"""
    # Arrange
    web = _REPO_ROOT / "simulator" / "sim_ui" / "web"
    app = build_sim_indicator_app(web_dir=web, data_root=tmp_path / "data")
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # Act
        index_status, _ = _get(base, "/")
        indicators_status, _ = _get(base, "/indicators")
        # Assert
        assert index_status == 200
        assert indicators_status == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
