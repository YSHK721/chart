"""run_job（子プロセス CLI）の結合検定。

固定する不変条件:
    1. 受ける引数は `--job-dir` **だけ**。仕様は job-dir の `spec.json` から読む
       （argv に仕様を並べない＝引数の取り違え・シェル経由のクォート事故を作らない）。
    2. `simulator.main.run_backtest` を `output_dir=<job-dir>` で呼ぶ。結果ペイロードは
       run_backtest の既存出力（stats.json / report.md）に限る。report_ui 形の
       report.json は Phase 4 の範囲（§8.1）なので**作らない**。
    3. **sizing OFF（既定）では `strategy_decorator` を渡さない**。
       §12.1「OFF は既存挙動と byte 等価」を、引数の不在という形で構造的に保証する。
    4. sizing ON のときだけ `strategy_decorator` を渡す（E-2 の引数名）。
    5. 終了コードは `run_backtest` の終了コードをそのまま返す
       （0=成功 / 1=BacktestError / 2=ConfigError。照合は `query_job` が行う）。

方式: `run_backtest` を差し替えて、**渡された引数**を観測する（重い実データを回さない）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.subprocess_job_launcher import SubprocessJobLauncher
from simulator.sim_ui.main import run_job


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    d = tmp_path / "0123456789abcdef0123456789abcdef"
    d.mkdir()
    return d


def _write_spec(job_dir: Path, *, sizing=None) -> None:
    (job_dir / "spec.json").write_text(
        json.dumps(
            {
                "backtest": {
                    "ea_name": "PRO_fit_Band_EA",
                    "symbol": "JP225",
                    "period": "M5",
                    "data_path": "/tmp/x.csv",
                },
                "sizing": sizing,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class _Spy:
    def __init__(self, exit_code: int = 0) -> None:
        self.kwargs = None
        self.exit_code = exit_code

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.exit_code, None


# --- 1. 引数 ---------------------------------------------------------------

def test_受ける引数はjob_dirだけ(job_dir: Path, monkeypatch) -> None:
    # Arrange
    _write_spec(job_dir)
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert spy.kwargs is not None


def test_job_dir以外の引数は受け付けない(job_dir: Path) -> None:
    # Act / Assert
    with pytest.raises(SystemExit):
        run_job.main(["--job-dir", str(job_dir), "--ea-name", "X"])


def test_仕様はspec_jsonから読む(job_dir: Path, monkeypatch) -> None:
    # Arrange
    _write_spec(job_dir)
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert spy.kwargs["ea_name"] == "PRO_fit_Band_EA"
    assert spy.kwargs["symbol"] == "JP225"
    assert spy.kwargs["data_path"] == "/tmp/x.csv"


def test_出力先はjob_dir(job_dir: Path, monkeypatch) -> None:
    """結果ペイロードはジョブディレクトリ配下に出る（/data/{job_id}/ で静的取得する）。"""
    # Arrange
    _write_spec(job_dir)
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert Path(spy.kwargs["output_dir"]) == job_dir.resolve()


# --- 2. サイジング（E-2 / §12.1）------------------------------------------

@pytest.mark.parametrize("sizing", [None, {"enabled": False}])
def test_sizingOFFではstrategy_decoratorを渡さない(
    job_dir: Path, monkeypatch, sizing
) -> None:
    """§12.1「既定 OFF ＝ 既存挙動と byte 等価」を引数の不在で保証する。"""
    # Arrange
    _write_spec(job_dir, sizing=sizing)
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert "strategy_decorator" not in spy.kwargs


def test_sizingONではstrategy_decoratorを渡す(job_dir: Path, monkeypatch) -> None:
    # Arrange
    _write_spec(job_dir, sizing={"enabled": True})
    spy = _Spy()
    sentinel = object()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    monkeypatch.setattr(run_job, "_build_decorator", lambda spec: sentinel)
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert spy.kwargs["strategy_decorator"] is sentinel


# --- 3. 終了コード ---------------------------------------------------------

@pytest.mark.parametrize("rc", [0, 1, 2])
def test_終了コードはrun_backtestの結果をそのまま返す(
    job_dir: Path, monkeypatch, rc: int
) -> None:
    # Arrange
    _write_spec(job_dir)
    monkeypatch.setattr(run_job, "run_backtest", _Spy(exit_code=rc))
    # Act
    got = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert got == rc


def test_spec_json不在は失敗の終了コードになる(job_dir: Path) -> None:
    """仕様が読めないジョブは黙って成功しない（照合側で「失敗」になる）。"""
    # Act
    got = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert got != 0


def test_例外は失敗の終了コードへ翻訳される(job_dir: Path, monkeypatch) -> None:
    """トレースバックで落ちても終了コードは非 0（＝失敗状態へ照合される）。"""
    # Arrange
    _write_spec(job_dir)

    def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_job, "run_backtest", _boom)
    # Act
    got = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert got != 0


# --- 4. 子プロセスとして起動できる形か -------------------------------------

def test_スクリプトとして直接実行できる(job_dir: Path) -> None:
    """`SubprocessJobLauncher` は `python run_job.py --job-dir ...` で起動する。

    環境（PYTHONPATH）は**本番と同一ソース**から取る。`run_job.py` を素の
    `python <script>` で起動すると `sys.path[0]` はスクリプトのあるディレクトリ
    （`simulator/sim_ui/main`）になり、`simulator` パッケージを import できない。
    本番は `SubprocessJobLauncher._child_env()` が repo 根を PYTHONPATH の先頭へ
    置くことで解決している（`subprocess_job_launcher.py:87-94`）。検定側で
    `PYTHONPATH` を書き下すと、本番の env 契約が変わったときに追随せず、
    「テストは緑だが本番は起動しない」を作る。
    """
    # Arrange（起動条件は本番の起動器から取得する）
    import subprocess

    repo_root = Path(run_job.__file__).resolve().parents[3]
    # import パスの導出は本番と同じ束縛（Composition Root が渡すもの）を使う（是正 1）。
    from simulator.sim_ui.main.composition_root_jobs import _dev_path_entries

    launcher = SubprocessJobLauncher(
        job_dir_of=lambda _id: job_dir,
        repo_root=repo_root,
        path_entries=_dev_path_entries,
    )
    script = Path(run_job.__file__).resolve()
    # Act（spec.json 不在 → 非 0 で即終了する。ここでは「起動して終われる」ことを見る）
    proc = subprocess.run(
        [sys.executable, str(script), "--job-dir", str(job_dir)],
        capture_output=True,
        timeout=60,
        cwd=str(repo_root),
        env=launcher._child_env(),
    )
    # Assert
    assert proc.returncode != 0
    assert b"Traceback" not in proc.stderr, "内部例外が呼び出し側へ漏れている"


# --- 5. Group A / Group B の継ぎ目（実物どうしで結線されているか）-----------

# なぜ必要か（実測された壊れ方）: 上の `test_sizingONではstrategy_decoratorを渡す` は
# `_build_decorator` を monkeypatch で差し替えているため、**継ぎ目そのものを検証していない**。
# 実際、初出の実装は `symbol_spec=backtest`（dict）を渡しており、
# `build_sizing_decorator` は `symbol_spec.volume_min`（属性アクセス）を要求するため
# `AttributeError` になっていた。例外は握られて exit=2 になるだけなので、
# **sizing ON のジョブが常に失敗する**のに監視上は「仕様エラー」としか見えない
# （ISSUE-291「受け口だけ作って呼び出し側が送らない」と同型）。実物どうしで固定する。

def _full_backtest_spec() -> dict:
    """build_interactor が実際に受け取る形（量制約を含む）。"""
    return {
        "ea_name": "TC24051901",
        "symbol": "EURUSD",
        "period": "M1",
        "data_path": "/tmp/x.csv",
        "initial_deposit": 100_000.0,
        "contract_size": 1.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "stops_level": 0,
        "digits": 5,
        "point_size": 0.0001,
        "leverage": 100.0,
        "ma_period": 2,
        "ma_method": "sma",
        "lot_size": 1.0,
        "stop_loss_points": 500,
        "take_profit_points": 3000,
    }


def test_build_decoratorは実物のsizingでStrategyPortを包める() -> None:
    """継ぎ目の実物検証（monkeypatch なし）。"""
    # Arrange
    spec = {
        "backtest": _full_backtest_spec(),
        "sizing": {"enabled": True, "sims": 5},
    }
    # Act
    decorator = run_job._build_decorator(spec)
    # Assert
    assert decorator is not None, "sizing ON なのに Decorator が作られていない"
    assert callable(decorator)


def test_build_decoratorが返す包装はStrategyPortである() -> None:
    from simulator.usecase.ports import StrategyPort

    # Arrange
    spec = {
        "backtest": _full_backtest_spec(),
        "sizing": {"enabled": True, "sims": 5},
    }

    class _Inner(StrategyPort):
        def on_init(self, config, indicators) -> None: ...
        def on_new_bar(self, bar_index, indicators, account):
            return []

        def on_position_check(self, position, bar_index, indicators) -> str:
            return "hold"

    # Act
    wrapped = run_job._build_decorator(spec)(_Inner())
    # Assert
    assert isinstance(wrapped, StrategyPort)


def test_build_decoratorは銘柄の量制約をsizingへ渡す() -> None:
    """量制約が渡っていないと丸めが銘柄仕様と食い違う（静かに誤ったロットになる）。"""
    # Arrange
    backtest = _full_backtest_spec()
    backtest.update(volume_min=0.5, volume_max=2.0, volume_step=0.5)
    spec = {"backtest": backtest, "sizing": {"enabled": True, "sims": 5}}

    class _Inner:
        def on_init(self, config, indicators) -> None: ...
        def on_new_bar(self, bar_index, indicators, account):
            return []

        def on_position_check(self, position, bar_index, indicators) -> str:
            return "hold"

    # Act
    wrapped = run_job._build_decorator(spec)(_Inner())
    rule = wrapped._sizing._rule
    # Assert
    assert (rule.volume_min, rule.volume_max, rule.volume_step) == (0.5, 2.0, 0.5)


def test_build_decoratorはentry_price_basisを反映する() -> None:
    """§12.2: 推定系列は約定価格基準で決まる。既定 close / current_open で open。"""
    # Arrange
    backtest = _full_backtest_spec()
    backtest["config_overrides"] = {"entry_price_basis": "current_open"}
    spec = {"backtest": backtest, "sizing": {"enabled": True, "sims": 5}}

    class _Inner:
        def on_init(self, config, indicators) -> None: ...
        def on_new_bar(self, bar_index, indicators, account):
            return []

        def on_position_check(self, position, bar_index, indicators) -> str:
            return "hold"

    # Act
    wrapped = run_job._build_decorator(spec)(_Inner())
    # Assert
    assert wrapped._price_series == "open"


def test_量制約が欠けた仕様は明示エラーになる() -> None:
    """欠落を黙って既定値で埋めると、銘柄と違う刻みのロットが静かに出る。"""
    # Arrange
    backtest = _full_backtest_spec()
    del backtest["volume_step"]
    spec = {"backtest": backtest, "sizing": {"enabled": True, "sims": 5}}
    # Act / Assert
    with pytest.raises(Exception):
        run_job._build_decorator(spec)


def test_sizingONのジョブが仕様エラーで落ちない(job_dir: Path, monkeypatch) -> None:
    """継ぎ目が壊れていると exit=2（仕様エラー）になる。実物経路で 0 を確認する。"""
    # Arrange
    (job_dir / "spec.json").write_text(
        json.dumps(
            {"backtest": _full_backtest_spec(), "sizing": {"enabled": True, "sims": 5}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert code == 0, "sizing ON の継ぎ目で落ちている"
    assert spy.kwargs["strategy_decorator"] is not None
    assert callable(spy.kwargs["strategy_decorator"])


# --- 6. 失敗理由の永続化（コードレビュー 🔴-3）-----------------------------

# 子プロセスは**状態を書かない**（本モジュール冒頭の設計）。一方で終了コードだけでは
# 「なぜ落ちたか」が運用者へ届かない（`BacktestController.run` は BacktestError を
# 終了コードのみへ翻訳し、文言はそこで消える）。理由**だけ**を `failure.json` に残し、
# 状態の確定は従来どおり sim core（`query_job`）が行う、という分担にする。
# launcher の stderr を PIPE にする案は採らない: 未読パイプが 64KB で埋まると
# 子がブロックして終わらなくなる。

def test_失敗理由がfailure_jsonに残る(job_dir: Path, monkeypatch) -> None:
    # Arrange
    _write_spec(job_dir)

    def _boom(**kwargs):
        raise RuntimeError("サイジング ON の発注には SL が必須です")

    monkeypatch.setattr(run_job, "run_backtest", _boom)
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert code != 0
    report = json.loads((job_dir / "failure.json").read_text(encoding="utf-8"))
    assert "SL が必須" in report["reason"]


def test_成功時はfailure_jsonを作らない(job_dir: Path, monkeypatch) -> None:
    """理由ファイルの存在自体が「失敗した」の印になるため、成功時に残さない。"""
    # Arrange
    _write_spec(job_dir)
    monkeypatch.setattr(run_job, "run_backtest", _Spy(exit_code=0))
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert not (job_dir / "failure.json").exists()


def test_仕様を読めない場合も理由を残す(job_dir: Path) -> None:
    """spec.json 不在（= 台帳と子の食い違い）も運用者に理由が要る。"""
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert code != 0
    assert (job_dir / "failure.json").is_file()


def test_子が状態ファイルを書かない(job_dir: Path, monkeypatch) -> None:
    """設計の維持: 状態は sim core が確定する（子は理由だけ残す）。"""
    # Arrange
    _write_spec(job_dir)

    def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_job, "run_backtest", _boom)
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert not (job_dir / "state.json").exists()
