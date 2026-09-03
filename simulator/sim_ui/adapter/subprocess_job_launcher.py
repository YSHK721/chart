"""A-SubprocessJobLauncher: 計算の子プロセスの起動・停止（:class:`JobLauncherPort` 実装）。

§12.7（依頼者裁定）:
    * 投入ごとに**独立の子プロセスを即時起動**する。待ち行列を作らず、同時 1 本にしない。
      プロセス並列のため GIL 律速（ISSUE-362）を受けず、「重い計算をサーバの request path に
      載せない」原則（ISSUE-362/364）も保たれる。
    * 子プロセスは sim core と**同一プロセスグループ**にする。すなわち **`setsid` しない**
      （`start_new_session` を渡さない）。`unified_ui/serve.sh` は sim core を PGID ごと
      kill して停止するため、新セッションを作ると子が孤児として残る。
    * 取消は **SIGTERM**（`Popen.terminate`）。SIGKILL は使わない。

責務（SRP）: **プロセスの生死だけ**。ジョブ状態の意味づけ（実行中か完了かなど）は持たない
（それは domain の遷移規則と `query_job` の照合が持つ）。

起動は `sys.executable`（sim core を動かしている venv python）で行う。生 python 起動は
依存不足で無音に死ぬため禁止（NFR-08）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from simulator.sim_ui.usecase.job_ports import JobLauncherPort
# 子へ渡す import パスは台帳（tools/dev_paths.txt）から**導出**する。ここで値を書き写すと
# 台帳と launcher の片方だけが腐る（ISSUE-279 で潰した「値の書き写し」の残り 1 件）。
# 導出関数は台帳の唯一の Python 消費者であり、.pth 生成器と同じものを読むだけである。
from tools.install_dev_paths import path_entries as ledger_path_entries

# 既定の子プロセス CLI（`--job-dir` のみを受ける）。
_DEFAULT_SCRIPT = Path(__file__).resolve().parents[1] / "main" / "run_job.py"
# repo 根 = simulator/sim_ui/adapter/subprocess_job_launcher.py の parents[3]。
_REPO_ROOT = Path(__file__).resolve().parents[3]


# SIGTERM 後に子の終了を待つ上限。取消は即応が要件（§12.7）なので長く待たない。
_TERMINATE_TIMEOUT_SEC = 10.0


class SubprocessJobLauncher(JobLauncherPort):
    """`run_job.py` を子プロセスとして起動する :class:`JobLauncherPort` 実装。

    ``job_dir_of``: ジョブ識別子 → ジョブディレクトリ（絶対パス）。台帳が採番規則を
      持つため、そちらの関数を注入して受ける（launcher は FS 配置を決めない）。
    ``script``: 子プロセス CLI のパス（既定は `run_job.py`）。検定で差し替える。
    """

    def __init__(
        self,
        *,
        job_dir_of: "Callable[[str], Any]",
        script: Any = None,
        repo_root: Any = None,
    ) -> None:
        self._job_dir_of = job_dir_of
        self._script = Path(script).resolve() if script else _DEFAULT_SCRIPT
        self._repo_root = Path(repo_root).resolve() if repo_root else _REPO_ROOT
        self._procs: "dict[str, subprocess.Popen]" = {}

    @property
    def python_executable(self) -> str:
        """子プロセスを起動する実行系（sim core と同じ venv python）。"""
        return sys.executable

    def launch(self, job_id: str) -> None:
        job_dir = Path(self._job_dir_of(job_id)).resolve()
        # start_new_session は渡さない（=setsid しない・§12.7）。
        self._procs[job_id] = subprocess.Popen(
            [
                self.python_executable,
                str(self._script),
                "--job-dir",
                str(job_dir),
            ],
            cwd=str(self._repo_root),
            env=self._child_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def terminate(self, job_id: str) -> None:
        proc = self._procs.get(job_id)
        if proc is None or proc.poll() is not None:
            return  # 未起動・回収済み。後継 PID を撃たない。
        proc.terminate()  # SIGTERM
        # SIGTERM を送っただけでは子は <defunct>（ゾンビ）として残る。親が wait して
        # 初めてカーネルがプロセスエントリを解放する。sim core は常駐プロセスなので、
        # 回収しないと取消のたびにゾンビが積み上がる。
        try:
            proc.wait(timeout=_TERMINATE_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            # SIGTERM に応じない子は残す（SIGKILL は結果ファイルの書きかけを生む）。
            # 次回 poll で回収されうるため _procs からは外さない。
            return
        # 回収済みの子は追跡表から外す（PID 再利用の取り違えを防ぐ）。
        self._procs.pop(job_id, None)

    def poll(self, job_id: str) -> "int | None":
        proc = self._procs.get(job_id)
        if proc is None:
            return None
        return proc.poll()

    def _child_env(self) -> "dict[str, str]":
        """台帳が定める import パスを PYTHONPATH の先頭へ置いた環境を返す。

        値をここに書き写さない（ISSUE-279）。「1 つのチェックアウトを構成する import
        パス」の唯一源は ``tools/dev_paths.txt`` であり、本メソッドはその 4 人目の消費者
        として**導出**する。以前は repo 根だけを置いていたが、それで動いていたのは
        共有 MA 実装を読む adapter が import 時に自分で ``sys.path`` を書き換えていた
        からであり、その書き換えを撤去した時点で子は
        ``ModuleNotFoundError: No module named 'moving_averages'`` で死んだ
        （ISSUE-479 Wave2 2-2 実測）。

        既存の ``PYTHONPATH`` は捨てず後ろへ継ぐ（呼び出し側の意図を壊さない）。
        既に載っているエントリは積み直さない（起動を繰り返しても増殖しない）。
        """
        env = dict(os.environ)
        existing = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        wanted = [str(p) for p in ledger_path_entries(self._repo_root)]
        merged = wanted + [p for p in existing if p not in wanted]
        env["PYTHONPATH"] = os.pathsep.join(merged)
        return env
