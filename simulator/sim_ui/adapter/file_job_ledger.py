"""A-FileJobLedger: FS 上のジョブ台帳（:class:`JobLedgerPort` 実装・adapter 層）。

配置（§6.1）: ジョブごとに ``<data_root>/<job_id>/`` を持ち、その中に
    ``spec.json``  投入仕様（子プロセスが読む。argv に仕様を載せない）
    ``state.json`` ジョブ状態（status / failure_reason）
    ``stats.json`` / ``report.md``  結果ペイロード（`run_backtest` が書く既存出力）
を置く。ブラウザからは ``/sim/data/{job_id}/stats.json`` で静的に取得する。

責務（SRP）: **永続化だけ**。子プロセスの生死は知らない（`SubprocessJobLauncher`）。
状態遷移の妥当性も判定しない（domain の `SimulationJob` が持つ）。本クラスは
「渡された状態を書き、書いたものを読み戻す」ことに徹する。

CWE-22: ``job_id`` は自分で採番した 16 進文字列に限って受理し、結果ファイル名も
単純名（区切り・``..`` を含まない）に限る。識別子もファイル名も URL 由来のため、
どちらからもディレクトリ外へ出られないようにする。
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from simulator.sim_ui.domain.simulation_job import (
    JobStatus,
    JobTransitionError,
    SimulationJob,
)
from simulator.sim_ui.usecase.job_models import JobSubmission
from simulator.sim_ui.usecase.job_ports import JobLedgerPort

_STATE_FILE = "state.json"
_SPEC_FILE = "spec.json"
# 子プロセスが残す失敗理由（状態は書かない・理由だけを残す）。
_FAILURE_FILE = "failure.json"
# 採番形式（uuid4 の hex 32 桁）。この形にだけ一致する識別子を受理する。
_JOB_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
# 結果ファイル名は単純名のみ（区切り・`..`・先頭ドットを許さない）。
_FILENAME_RE = re.compile(r"\A[A-Za-z0-9_-]+\.[A-Za-z0-9]+\Z")


class FileJobLedger(JobLedgerPort):
    """``data_root`` 配下にジョブ台帳を持つ :class:`JobLedgerPort` 実装。"""

    def __init__(self, *, data_root: Any) -> None:
        # cwd 非依存の絶対パスに固定する（起動場所で台帳の位置が変わらないように）。
        self._root = Path(data_root).resolve()
        # compare-and-set の比較〜書き込みを不可分にする（同一プロセス内の並行更新）。
        self._lock = threading.Lock()

    # --- JobLedgerPort ---------------------------------------------------

    def create(self, submission: JobSubmission) -> SimulationJob:
        job_id = uuid.uuid4().hex
        job_dir = self._root / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        (job_dir / _SPEC_FILE).write_text(
            json.dumps(
                {"backtest": dict(submission.backtest),
                 "sizing": dict(submission.sizing) if submission.sizing else None,
                 # Phase 6 F-8（P6-E1）: 戦略項目ブロック（backtest/sizing の兄弟）。
                 # 不在時は null（既定 OFF＝子プロセス側の解釈は byte 等価）。
                 "strategy": dict(submission.strategy) if submission.strategy else None,
                 # Phase 8 §18（T-4）: Tester Settings ブロック（第 4 ブロック）。
                 # 不在時は null＝旧 spec と併存し、子プロセスは現行経路を通る。
                 "settings": dict(submission.settings) if submission.settings else None},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        job = SimulationJob.received(job_id)
        # 初回書き込みは compare-and-set の対象外（比較すべき先行状態が無い）。
        self._write_state(job)
        return job

    def load(self, job_id: str) -> "SimulationJob | None":
        if not _JOB_ID_RE.match(job_id or ""):
            return None
        state_file = self._root / job_id / _STATE_FILE
        if not state_file.is_file():
            return None
        data = json.loads(state_file.read_text(encoding="utf-8"))
        # 読み戻しは「遷移」ではなく**再水和**のため、遷移表を通さず直接構築する
        # （保存済みの終端状態を復元するのに RECEIVED から辿り直す必要はない）。
        return SimulationJob(
            job_id=job_id,
            status=JobStatus(data["status"]),
            failure_reason=data.get("failure_reason"),
        )

    def update(self, job: SimulationJob, *, expect: JobStatus) -> None:
        """compare-and-set。永続状態が ``expect`` でなければ書かずに例外。

        単一プロセス内の複数スレッド（`ThreadingHTTPServer`）が同じジョブを更新しうる
        ため、比較と書き込みをロックで囲む。異プロセス間は本 Phase の対象外
        （sim core は 1 プロセス・§3.1）。
        """
        with self._lock:
            current = self.load(job.job_id)
            if current is None:
                raise JobTransitionError(
                    f"台帳に存在しないジョブは更新できません: {job.job_id}"
                )
            if current.status is not expect:
                raise JobTransitionError(
                    "ジョブ状態が期待と異なるため更新を破棄しました "
                    f"(job_id={job.job_id} 期待={expect.value} 実際={current.status.value} "
                    f"書こうとした値={job.status.value})"
                )
            self._write_state(job)

    def read_failure_report(self, job_id: str) -> "str | None":
        """子プロセスが残した失敗理由（`failure.json`）を読む。無ければ None。"""
        try:
            path = self.job_dir(job_id) / _FAILURE_FILE
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        reason = data.get("reason") if isinstance(data, dict) else None
        return str(reason) if reason else None

    def _write_state(self, job: SimulationJob) -> None:
        state_file = self.job_dir(job.job_id) / _STATE_FILE
        # 同一ディレクトリへ書いてから rename（同一 FS の原子的置換）。
        # 照会側が「書きかけの state.json」を読むことを防ぐ（§12.7 並列実行）。
        # 一時ファイル名は**書き手ごとに一意**にする。ジョブ内で固定名にすると、
        # 同一ジョブを 2 スレッドが同時に書いたとき片方の rename が他方の tmp を
        # 消して FileNotFoundError になる（実測）。
        tmp = state_file.with_name(f"state.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(
                json.dumps(
                    {"job_id": job.job_id,
                     "status": job.status.value,
                     "failure_reason": job.failure_reason},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            tmp.replace(state_file)
        finally:
            # 置換に失敗した場合でも残骸を残さない。
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def result_path(self, job_id: str, filename: str) -> Path:
        if not _FILENAME_RE.match(filename or ""):
            raise ValueError(f"結果ファイル名として受理できません: {filename!r}")
        return self.job_dir(job_id) / filename

    # --- adapter 固有（Port の一部ではない・合成根と起動器が使う）---------

    def job_dir(self, job_id: str) -> Path:
        """ジョブディレクトリの絶対パスを返す。識別子が採番形式でなければ ValueError。"""
        if not _JOB_ID_RE.match(job_id or ""):
            raise ValueError(f"ジョブ識別子として受理できません: {job_id!r}")
        return self._root / job_id

    @property
    def data_root(self) -> Path:
        """台帳の根（静的配信の根としても使う）。"""
        return self._root
