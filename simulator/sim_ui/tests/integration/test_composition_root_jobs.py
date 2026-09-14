"""composition_root_jobs（ジョブ対応 sim core の DI 結線・main 層）の結合検定。

固定する不変条件:
    1. 配信面（web_dir / shared_js_root）は Phase 1 の `build_sim_app` と同じ規約で解決する。
    2. 台帳の根（data_root）は既定で `<repo>/simulator/sim_ui/data`。`repo_root` を
       差し替えれば追随する（既定値の出所を 1 つに保つ）。
       起動スクリプトに新しい変数を足さずに済ませるため、既定値を持たせている。
    3. 台帳・起動器・系列カタログが実物で結線される（フェイクが本番へ漏れない）。
    4. 起動器のジョブディレクトリ解決は**台帳の採番規則**を使う（二重定義しない）。
    5. `simulator/sim_ui/web` を配信根に取れる（起動スクリプトが渡す実物）。
"""
from __future__ import annotations

import json
from pathlib import Path

from simulator.sim_ui.adapter.ea_registry_series_catalog import EaRegistrySeriesCatalog
from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.adapter.subprocess_job_launcher import SubprocessJobLauncher
from simulator.sim_ui.framework.serve_sim_jobs import SimJobApp
from simulator.sim_ui.main.composition_root_jobs import build_sim_job_app

_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_ジョブ対応のアプリが組み上がる(tmp_path: Path) -> None:
    # Arrange / Act
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert isinstance(app, SimJobApp)


def test_実物のPort実装が結線される(tmp_path: Path) -> None:
    # Arrange / Act
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert isinstance(app.ledger, FileJobLedger)
    assert isinstance(app.launcher, SubprocessJobLauncher)


def test_台帳の根は既定でsim_uiのdata配下(tmp_path: Path) -> None:
    # Arrange / Act
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert app.ledger.data_root == (tmp_path / "simulator" / "sim_ui" / "data").resolve()


def test_台帳の根は明示指定できる(tmp_path: Path) -> None:
    # Arrange / Act
    app = build_sim_job_app(
        repo_root=tmp_path, web_dir=tmp_path / "web", data_root=tmp_path / "jobs"
    )
    # Assert
    assert app.ledger.data_root == (tmp_path / "jobs").resolve()


def test_起動器は台帳の採番規則でジョブディレクトリを解決する(tmp_path: Path) -> None:
    """FS 配置の知識を起動器に二重定義しない。"""
    # Arrange
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    from simulator.sim_ui.usecase.job_models import JobSubmission

    job = app.ledger.create(JobSubmission(backtest={"ea_name": "X"}))
    # Act
    resolved = app.launcher._job_dir_of(job.job_id)
    # Assert
    assert Path(resolved) == app.ledger.job_dir(job.job_id)


def test_系列カタログは実物(tmp_path: Path) -> None:
    """E-3 判定がエンジンの公開アクセサを単一ソースとして見ていること。

    合成根が結線を持つこと（`app.controller`）に加え、**その結線が実際に解決できる**
    ことを測る。従来は `isinstance(EaRegistrySeriesCatalog(), EaRegistrySeriesCatalog)`
    という恒真式で、束縛が壊れても落ちなかった（ISSUE-405 で束縛点が移ったため是正）。
    """
    # Arrange / Act
    from simulator.sim_ui.main.composition_root_jobs import build_series_catalog

    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    catalog = build_series_catalog()
    # Assert（実際の系列集合は catalog 自身の検定が固定する）
    assert isinstance(catalog, EaRegistrySeriesCatalog)
    assert catalog.series_for("TC24051901") == frozenset({"madiff", "close"})
    assert app.controller is not None


def test_共有JS根は既定でindicator_uiのweb(tmp_path: Path) -> None:
    """Phase 1 の `build_sim_app` と同じ既定（配信面の規約を変えない）。"""
    # Arrange / Act
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web")
    # Assert
    assert app.shared_js_root == (
        tmp_path / "indigators" / "indicator_ui" / "web"
    ).resolve()


def test_実物のweb根を配信できる() -> None:
    """起動スクリプトが渡す `simulator/sim_ui/web` が実在し、配信根に取れる。"""
    # Arrange
    web = _REPO_ROOT / "simulator" / "sim_ui" / "web"
    assert (web / "index.html").is_file()
    # Act
    app = build_sim_job_app(web_dir=web)
    # Assert
    assert app.web_dir == web.resolve()


def _minimal_backtest() -> "dict":
    """`build_interactor` の必須引数をすべて埋めた最小の実行仕様。

    値は疎通確認用（本検定では子プロセスの中身を見ない）。必須集合は本番と同一ソース
    から引く（手書きしない）。
    """
    from simulator.sim_ui.main.composition_root_jobs import required_backtest_keys

    backtest = {name: 0 for name in required_backtest_keys()}
    backtest["ea_name"] = "PRO_fit_Band_EA"
    return backtest


# --- 起動スクリプトが通る経路の端から端まで --------------------------------

def test_起動スクリプトと同じ結線で実HTTPが応答する(tmp_path: Path) -> None:
    """`unified_ui/serve.sh` の `start_sim_core` が使う 2 モジュールをそのまま起動する。

    受け口だけ作って**呼び出し側が送らない**という壊れ方（ISSUE-291）を防ぐため、
    合成根 → framework → 実 HTTP までを 1 本で通す。ここが緑なら、起動スクリプトの
    import 行が指すものが実際に起動して応答することが実証される。
    固定ポートは掴まない（port=0）。
    """
    # Arrange（serve.sh の import 行と同一の 2 モジュール）
    import threading
    import urllib.request

    from simulator.sim_ui.framework.serve_sim_jobs import make_server

    web = _REPO_ROOT / "simulator" / "sim_ui" / "web"
    app = build_sim_job_app(web_dir=web, data_root=tmp_path / "data")
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # Act（静的配信＝Phase 1 の疎通条件）
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            index_status = response.status
        # Act（ジョブ API＝Phase 2 の受け口が実在するか）
        request = urllib.request.Request(
            base + "/jobs",
            # 本番の合成根は必須キー検査を課す（🟡-A）。実結線の疎通を見るのが目的なので
            # 必須キーを満たした最小の本文を送る。
            data=json.dumps({"backtest": _minimal_backtest()}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            submit_status = response.status
            submitted = json.loads(response.read())
        # Assert
        assert index_status == 200
        assert submit_status == 202
        assert submitted["job_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# --- 失敗理由が実プロセス経由で台帳へ届く（コードレビュー 🔴-3）------------

class _NoStopLossParams:
    """SL 系設定パラメータを持たないカタログ（受付では判定しない）。"""

    @staticmethod
    def stop_loss_params(ea_name: str) -> "frozenset[str]":
        return frozenset()


def _no_stop_distance_csv(path: Path) -> Path:
    """既定 TC 経路の最小 CSV（MADiff SMA period=2 で bar2 に買いクロス）。"""
    rows = [
        # time は UNIX 秒 int（UTC・2024-01-01T00:00:00Z=1704067200）。comma 形式 CSV の `time` は epoch 秒が契約であり（Candle 契約 §2.1）、ISO 文字列は `Bar.time` 契約違反になる。
        (1704067200, 1.1000, 1.1010, 1.0990, 1.0995, 1.0, 0),
        (1704067260, 1.1000, 1.1010, 1.0985, 1.0990, 1.0, 0),
        (1704067320, 1.0990, 1.1050, 1.0990, 1.1040, 1.0, 0),
        (1704067380, 1.1040, 1.1100, 1.1040, 1.1090, 1.0, 0),
        (1704067440, 1.1090, 1.1120, 1.0900, 1.0950, 1.0, 0),
        (1704067500, 1.0950, 1.0960, 1.0900, 1.0920, 1.0, 0),
    ]
    lines = ["time,open,high,low,close,volume,spread"]
    for t, o, h, l, c, v, sp in rows:
        lines.append(f"{t},{o},{h},{l},{c},{v},{sp}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_SL不在のfail_stop理由が実プロセス経由で台帳へ届く(tmp_path: Path) -> None:
    """🔴-3 の本命。子プロセスを**実際に起動**して理由の伝播を端から端まで見る。

    `stop_loss_points=0` は SL が建値と同一（リスク距離 0）になる実 EA 設定。
    受付検証（§12.8）は本来ここで拒否するが、本検定は【従】fail-stop の理由伝播を
    見るため、受付判定を行わないカタログを注入して実行段へ通す。
    """
    import time

    from simulator.sim_ui.domain.simulation_job import JobStatus
    from simulator.sim_ui.usecase.job_models import JobSubmission

    # Arrange
    csv_path = _no_stop_distance_csv(tmp_path / "m1.csv")
    app = build_sim_job_app(
        repo_root=_REPO_ROOT, web_dir=tmp_path / "web", data_root=tmp_path / "jobs"
    )
    submission = JobSubmission(
        backtest={
            "ea_name": "TC24051901", "symbol": "EURUSD", "period": "M1",
            "data_path": str(csv_path), "initial_deposit": 100_000.0,
            "contract_size": 1.0, "volume_min": 0.01, "volume_max": 100.0,
            "volume_step": 0.01, "stops_level": 0, "digits": 5,
            "point_size": 0.0001, "leverage": 100.0, "ma_period": 2,
            "ma_method": "sma", "lot_size": 1.0,
            "stop_loss_points": 0, "take_profit_points": 3000,
        },
        sizing={"enabled": True, "sims": 20},
    )
    # 受付の SL 検証は本検定の対象外（実行段の理由伝播を見る）。
    app.controller._submit._stop_loss_catalog = _NoStopLossParams()

    # Act
    job = app.controller._submit.execute(submission)
    deadline = time.time() + 180
    view = None
    while time.time() < deadline:
        view = app.controller._query.execute(job.job_id)
        if view.status != JobStatus.RUNNING.value:
            break
        time.sleep(0.2)

    # Assert
    assert view is not None
    assert view.status == JobStatus.FAILED.value, f"無音で終わっている: {view.status}"
    assert view.failure_reason, "失敗理由が空"
    assert "SL" in view.failure_reason, f"理由に SL の旨が無い: {view.failure_reason!r}"


# ---- 受付検査（trace の期間）が実 Composition Root から届いていること（ISSUE-508 §6.4）----

#: `required_backtest_keys()` を満たす最小の実行仕様（値は本検定では使わない）。
_TRACE_PROBE_BACKTEST = {
    "data_path": "/nonexistent/probe.csv", "symbol": "EURUSD", "period": "M1",
    "ea_name": "TC24051901", "initial_deposit": 100_000.0, "contract_size": 1.0,
    "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01, "stops_level": 0,
    "digits": 5, "point_size": 0.0001, "leverage": 100.0, "ma_period": 2,
    "ma_method": "sma", "lot_size": 1.0, "stop_loss_points": 100,
    "take_profit_points": 200,
}
_TRACE_PROBE_EPOCH = 1_704_067_200


def _submit_through_the_real_root(app, monkeypatch, trace: dict):
    """実 Composition Root の受付面へ本文を通す（子プロセスの起動だけ止める）。

    `app.launcher` は投入 Interactor が保持している**同じ実体**なので、
    その起動メソッドを差し替えれば起動だけが止まり、受付検証・台帳・状態遷移は
    すべて実物のまま通る（フェイクの合成根を組み直すと、まさに検査したい
    「本番の結線」が検査対象から外れる）。
    """
    monkeypatch.setattr(app.launcher, "launch", lambda job_id: None)
    body = {"backtest": dict(_TRACE_PROBE_BACKTEST), "trace": trace}
    return app.controller.submit(json.dumps(body).encode("utf-8"))


def test_実合成根から実行トレースの期間検査が届いている(tmp_path: Path, monkeypatch) -> None:
    """`trace_window_check` の注入が本番の合成根で結線されていること。

    なぜ必要か（実測した壊れ方）: `simulator/sim_ui/main/composition_root_jobs.py` と
    `simulator/sim_ui/framework/serve_sim_jobs.py` のどちらか一方で注入を `None` にしても
    **sim_ui の全検定が緑のまま**だった。結線が切れると受付段（`simulator/sim_ui/
    usecase/submit_job.py`）は trace ON の
    投入を**すべて拒否**し、機能が完全に死ぬ。§6.6.1 の結線ゲートは body / ledger /
    spec の 3 ホップだけを見ており、この注入ホップを見ていない。

    **受理側と拒否側を必ず対にする**: 拒否側だけを測ると「何を投入しても 400」を
    返す構成（＝結線が切れた状態）が緑のまま通る。受理側が正の対照である。
    """
    # Arrange
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web",
                            data_root=tmp_path / "jobs")

    # Act: 正しい窓の trace ON 投入。
    accepted = _submit_through_the_real_root(
        app, monkeypatch,
        {"enabled": True, "start": _TRACE_PROBE_EPOCH, "end": _TRACE_PROBE_EPOCH + 3600},
    )

    # Assert: 受理される（結線が切れていればここが 400 になる）。
    assert accepted.status == 202, accepted.payload
    # 台帳に trace ブロックが届いている（受理が形だけでないことの実証）。
    spec = json.loads(
        (app.ledger.job_dir(accepted.payload["job_id"]) / "spec.json").read_text("utf-8")
    )
    assert spec["trace"] == {
        "enabled": True, "start": _TRACE_PROBE_EPOCH, "end": _TRACE_PROBE_EPOCH + 3600
    }


def test_実合成根が逆転した記録期間を期間の誤りとして拒む(tmp_path: Path, monkeypatch) -> None:
    """拒否側。**理由まで縛る**ことで「結線が切れて全部拒否」と区別する。

    結線が切れた構成も 400 を返すため、状態コードだけでは両者を判別できない。
    文言が「期間を解釈できない」ことを述べていれば、検査が実際に走った証拠になる。
    """
    # Arrange
    app = build_sim_job_app(repo_root=tmp_path, web_dir=tmp_path / "web",
                            data_root=tmp_path / "jobs")

    # Act: 開始が終了より後。
    refused = _submit_through_the_real_root(
        app, monkeypatch,
        {"enabled": True, "start": _TRACE_PROBE_EPOCH + 3600, "end": _TRACE_PROBE_EPOCH},
    )

    # Assert
    assert refused.status == 400
    message = refused.payload["error"]
    assert "trace の記録期間を解釈できません" in message, message
    assert "結線されていません" not in message, message
