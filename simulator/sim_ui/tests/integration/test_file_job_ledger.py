"""FileJobLedger（FS 上のジョブ台帳・adapter）の結合検定。

固定する不変条件:
    1. ジョブごとに `<data_root>/<job_id>/` を持ち、投入仕様（spec.json）と状態
       （state.json）を保存する。子プロセスはこの spec.json を読んで走る。
    2. 状態の往復（保存→読み出し）で状態・失敗理由が保たれる。
    3. **並列に複数ジョブを扱ってもジョブ間で状態が混ざらない**（§12.7）。
    4. 識別子は台帳が採番し、重複しない。
    5. 結果の所在は自分のジョブディレクトリの中に限る（`..` や絶対パスで外へ出ない）。
       ジョブ識別子・ファイル名のどちらからも外へ出られないこと（CWE-22）。

方式: tmp_path 上の実 FS（実データ・実 I/O 経路。合成の in-memory FS は使わない）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.domain.simulation_job import (
    JobStatus,
    JobTransitionError,
    SimulationJob,
)
from simulator.sim_ui.tests.integration._fake_ports import FakeLauncher, submission
from simulator.sim_ui.usecase.cancel_job import CancelJobInteractor
from simulator.sim_ui.usecase.job_models import JobSubmission
from simulator.sim_ui.usecase.query_job import QueryJobInteractor


@pytest.fixture
def ledger(tmp_path: Path) -> FileJobLedger:
    return FileJobLedger(data_root=tmp_path / "data")


def _submission(ea_name: str = "PRO_fit_Band_EA") -> JobSubmission:
    return JobSubmission(
        backtest={"ea_name": ea_name, "symbol": "JP225", "period": "M5"},
        sizing={"enabled": True},
    )


# --- 1. 登録 ---------------------------------------------------------------

def test_登録すると受付状態のジョブが返る(ledger: FileJobLedger) -> None:
    # Arrange / Act
    job = ledger.create(_submission())
    # Assert
    assert job.status is JobStatus.RECEIVED
    assert job.job_id


def test_登録するとジョブディレクトリと投入仕様が作られる(
    ledger: FileJobLedger, tmp_path: Path
) -> None:
    """子プロセスは argv ではなく spec.json から仕様を読む（引数の取り違えを作らない）。"""
    # Arrange / Act
    job = ledger.create(_submission("WeeklyVolBand_EA"))
    # Assert
    job_dir = tmp_path / "data" / job.job_id
    assert job_dir.is_dir()
    spec = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
    assert spec["backtest"]["ea_name"] == "WeeklyVolBand_EA"
    assert spec["sizing"] == {"enabled": True}


def test_採番は重複しない(ledger: FileJobLedger) -> None:
    # Arrange / Act
    ids = {ledger.create(_submission()).job_id for _ in range(20)}
    # Assert
    assert len(ids) == 20


def test_ジョブディレクトリの絶対パスが取れる(
    ledger: FileJobLedger, tmp_path: Path
) -> None:
    """子プロセスへ渡す --job-dir は cwd 非依存の絶対パスであること。"""
    # Arrange
    job = ledger.create(_submission())
    # Act
    got = ledger.job_dir(job.job_id)
    # Assert
    assert got.is_absolute()
    assert got == (tmp_path / "data" / job.job_id).resolve()


# --- 2. 往復 ---------------------------------------------------------------

def test_未知の識別子はNoneを返す(ledger: FileJobLedger) -> None:
    assert ledger.load("no-such-job") is None


def test_状態の往復で状態が保たれる(ledger: FileJobLedger) -> None:
    # Arrange
    job = ledger.create(_submission())
    running = job.to(JobStatus.RUNNING)
    # Act
    ledger.update(running, expect=JobStatus.RECEIVED)
    # Assert
    assert ledger.load(job.job_id).status is JobStatus.RUNNING


def test_失敗理由も往復で保たれる(ledger: FileJobLedger) -> None:
    # Arrange
    job = ledger.create(_submission()).to(JobStatus.RUNNING)
    ledger.update(job, expect=JobStatus.RECEIVED)
    failed = job.to(JobStatus.FAILED, failure_reason="終了コード 1")
    # Act
    ledger.update(failed, expect=JobStatus.RUNNING)
    # Assert
    loaded = ledger.load(job.job_id)
    assert loaded.status is JobStatus.FAILED
    assert loaded.failure_reason == "終了コード 1"


def test_読み出したジョブは遷移規則に従う(ledger: FileJobLedger) -> None:
    """読み戻しても domain の不変条件が効くこと（状態機械を迂回しない）。"""
    # Arrange
    job = ledger.create(_submission()).to(JobStatus.RUNNING)
    ledger.update(job, expect=JobStatus.RECEIVED)
    ledger.update(job.to(JobStatus.CANCELLED), expect=JobStatus.RUNNING)
    # Act
    loaded = ledger.load(job.job_id)
    # Assert
    assert isinstance(loaded, SimulationJob)
    from simulator.sim_ui.domain.simulation_job import JobTransitionError

    with pytest.raises(JobTransitionError):
        loaded.to(JobStatus.COMPLETED)


# --- 3. 並列（§12.7）------------------------------------------------------

def test_複数ジョブの状態が混ざらない(ledger: FileJobLedger) -> None:
    """§12.7 並列実行。1 つのジョブの更新が他へ波及しないこと。"""
    # Arrange
    a = ledger.create(_submission("PRO_fit_Band_EA"))
    b = ledger.create(_submission("WeeklyVolBand_EA"))
    c = ledger.create(_submission("StopEntryProbe_EA"))
    # Act（a だけ完了・b だけ失敗・c は受付のまま）
    ledger.update(a.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    ledger.update(
        a.to(JobStatus.RUNNING).to(JobStatus.COMPLETED), expect=JobStatus.RUNNING
    )
    ledger.update(b.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    ledger.update(
        b.to(JobStatus.RUNNING).to(JobStatus.FAILED, failure_reason="boom"),
        expect=JobStatus.RUNNING,
    )
    # Assert
    assert ledger.load(a.job_id).status is JobStatus.COMPLETED
    assert ledger.load(b.job_id).status is JobStatus.FAILED
    assert ledger.load(b.job_id).failure_reason == "boom"
    assert ledger.load(c.job_id).status is JobStatus.RECEIVED
    assert ledger.load(a.job_id).failure_reason is None


def test_複数ジョブの投入仕様が混ざらない(ledger: FileJobLedger) -> None:
    # Arrange
    a = ledger.create(_submission("PRO_fit_Band_EA"))
    b = ledger.create(_submission("WeeklyVolBand_EA"))
    # Act
    spec_a = json.loads((ledger.job_dir(a.job_id) / "spec.json").read_text("utf-8"))
    spec_b = json.loads((ledger.job_dir(b.job_id) / "spec.json").read_text("utf-8"))
    # Assert
    assert spec_a["backtest"]["ea_name"] == "PRO_fit_Band_EA"
    assert spec_b["backtest"]["ea_name"] == "WeeklyVolBand_EA"


# --- 4. 結果の所在（CWE-22）-----------------------------------------------

def test_結果の所在はジョブディレクトリ配下(ledger: FileJobLedger) -> None:
    # Arrange
    job = ledger.create(_submission())
    # Act
    got = ledger.result_path(job.job_id, "stats.json")
    # Assert
    assert got == ledger.job_dir(job.job_id) / "stats.json"


@pytest.mark.parametrize(
    "bad", ["../../etc/passwd", "..", "a/b", "/etc/passwd", "sub/../../x"]
)
def test_ファイル名で外へ出られない(ledger: FileJobLedger, bad: str) -> None:
    # Arrange
    job = ledger.create(_submission())
    # Act / Assert
    with pytest.raises(ValueError):
        ledger.result_path(job.job_id, bad)


@pytest.mark.parametrize("bad", ["../other", "..", "a/b", "/abs"])
def test_ジョブ識別子で外へ出られない(ledger: FileJobLedger, bad: str) -> None:
    """識別子は URL から来る。台帳が受理する形を採番形式に限定する。"""
    # Act / Assert
    assert ledger.load(bad) is None
    with pytest.raises(ValueError):
        ledger.job_dir(bad)


# --- 並行更新の安全性（コードレビュー 🔴-1 / 🔴-2）------------------------

# 🔴-1 実測された壊れ方: `update` が無条件書き込みだったため、cancel が CANCELLED を
#   書いた直後に query が（古い読みに基づく）COMPLETED を上書きし、**取消したジョブの
#   結果が公開される**（§12.7「取消＝終端確定・部分結果非公開」の破れ）。
# 🔴-2 実測された壊れ方: 一時ファイル名がジョブ内で固定（`state.json.tmp`）のため、
#   同一ジョブを 2 スレッドが同時に update すると、片方の rename が他方の tmp を
#   消して `FileNotFoundError` になる。

def test_取消済みジョブをqueryが完了で上書きしない(tmp_path: Path) -> None:
    """🔴-1 の中核。実 FileJobLedger + 実 Interactor で固定する。"""
    # Arrange: 実行中まで進めたジョブ（子は終了コード 0 を返す＝完了候補）
    ledger = FileJobLedger(data_root=tmp_path)
    launcher = FakeLauncher()
    job = ledger.create(submission())
    ledger.update(job.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    launcher.launched.append(job.job_id)
    launcher.finish(job.job_id, return_code=0)

    query = QueryJobInteractor(ledger=ledger, launcher=launcher)
    cancel = CancelJobInteractor(ledger=ledger, launcher=launcher)

    # Act: 取消が先に確定 → その後で query が古い読みに基づいて照合しようとする
    cancel.execute(job.job_id)
    view = query.execute(job.job_id)

    # Assert: 終端は取消のまま（完了へ戻らない）
    assert view.status == JobStatus.CANCELLED.value
    assert ledger.load(job.job_id).status is JobStatus.CANCELLED


def test_期待状態と違えば書き込まない(tmp_path: Path) -> None:
    """compare-and-set の本体。期待と違う永続状態は上書きしない。"""
    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    job = ledger.create(submission())
    ledger.update(job.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    # Act / Assert（永続は RUNNING なのに RECEIVED を期待して書く）
    with pytest.raises(JobTransitionError):
        ledger.update(
            job.to(JobStatus.RUNNING).to(JobStatus.COMPLETED),
            expect=JobStatus.RECEIVED,
        )
    assert ledger.load(job.job_id).status is JobStatus.RUNNING


def test_期待状態が一致すれば書き込む(tmp_path: Path) -> None:
    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    job = ledger.create(submission())
    # Act
    ledger.update(job.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    # Assert
    assert ledger.load(job.job_id).status is JobStatus.RUNNING


def test_並行cancelとqueryで終端が1つに定まる(tmp_path: Path) -> None:
    """🔴-1 を並行実行で確認する（勝者がどちらでも終端は 1 つ）。"""
    import threading

    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    launcher = FakeLauncher()
    job = ledger.create(submission())
    ledger.update(job.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    launcher.launched.append(job.job_id)
    launcher.finish(job.job_id, return_code=0)
    query = QueryJobInteractor(ledger=ledger, launcher=launcher)
    cancel = CancelJobInteractor(ledger=ledger, launcher=launcher)
    start = threading.Barrier(2)
    errors: "list[BaseException]" = []

    def _cancel() -> None:
        start.wait()
        try:
            cancel.execute(job.job_id)
        except JobTransitionError:
            pass          # 既に完了していた＝正当な敗北
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _query() -> None:
        start.wait()
        try:
            query.execute(job.job_id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    # Act
    threads = [threading.Thread(target=_cancel), threading.Thread(target=_query)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # Assert
    assert errors == [], f"並行更新で想定外の例外: {errors}"
    final = ledger.load(job.job_id)
    assert final.status.is_terminal
    # 一度終端になったらもう動かない
    assert query.execute(job.job_id).status == final.status.value


def test_同一ジョブの並行updateで一時ファイルが衝突しない(tmp_path: Path) -> None:
    """🔴-2: 一時ファイル名が固定だと rename 競合で FileNotFoundError になる。"""
    import threading

    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    job = ledger.create(submission())
    running = job.to(JobStatus.RUNNING)
    ledger.update(running, expect=JobStatus.RECEIVED)
    errors: "list[BaseException]" = []
    start = threading.Barrier(8)

    def _hammer() -> None:
        start.wait()
        for _ in range(40):
            try:
                # 同じ状態を書き続ける（CAS は通る）。競合するのは一時ファイルだけ。
                ledger.update(running, expect=JobStatus.RUNNING)
            except JobTransitionError:
                pass
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                return

    # Act
    threads = [threading.Thread(target=_hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # Assert
    assert errors == [], f"並行 update で例外: {errors[:3]}"
    assert ledger.load(job.job_id).status is JobStatus.RUNNING


def test_一時ファイルが残らない(tmp_path: Path) -> None:
    """書き手ごとに一意な tmp を使っても、置換後に残骸を残さない。"""
    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    job = ledger.create(submission())
    # Act
    ledger.update(job.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    # Assert
    leftovers = list(ledger.job_dir(job.job_id).glob("*.tmp"))
    assert leftovers == [], f"一時ファイルが残っている: {leftovers}"


def test_load後cancelが割り込んでもqueryが完了で上書きしない(tmp_path: Path) -> None:
    """🔴-1 の本命。**決定的に競合を差し込んで** CAS が効くことを固定する。

    上の逐次版（取消 → 照会）は、照会が「終端なら再照合しない」で早期 return するため
    CAS を通らない。実際に危険なのは
        query が RUNNING を load → その隙に cancel が CANCELLED を書く → query が書く
    という順序である。`poll` の呼び出し（load と update の間）に cancel を差し込んで
    この順序を再現する。
    """
    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    job = ledger.create(submission())
    ledger.update(job.to(JobStatus.RUNNING), expect=JobStatus.RECEIVED)
    cancel = CancelJobInteractor(ledger=ledger, launcher=FakeLauncher())

    class _RacingLauncher(FakeLauncher):
        """`poll`（load と update の間）で取消を割り込ませる起動器。"""

        def poll(self, job_id: str) -> "int | None":
            cancel.execute(job_id)   # ← ここで CANCELLED が確定する
            return 0                 # 子は成功で終わっていた、という報告

    query = QueryJobInteractor(ledger=ledger, launcher=_RacingLauncher())

    # Act
    view = query.execute(job.job_id)

    # Assert: 取消が終端として残り、完了で塗り替えられない（§12.7）
    assert view.status == JobStatus.CANCELLED.value, (
        "取消が完了で上書きされた（部分結果が公開される）"
    )
    assert ledger.load(job.job_id).status is JobStatus.CANCELLED
