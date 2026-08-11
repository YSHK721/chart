"""SubprocessJobLauncher（子プロセス起動器・adapter）の結合検定。

固定する不変条件（§12.7 依頼者裁定）:
    1. 投入ごとに**独立の子プロセス**を即時起動する。同時 1 本に直列化しない
       （実プロセスを 2 本同時に走らせて両方の生存を確認する）。
    2. 子プロセスは sim core と**同一プロセスグループ**（`setsid` しない）。
       serve.sh の PGID kill で一緒に回収されるため。新セッションを作ると孤児になる。
    3. `terminate` は **SIGTERM**（SIGKILL ではない）を送る。
    4. `poll` は実行中なら None、終了後は終了コードを返す。
    5. 起動は `sys.executable`（venv python）で行う。生 python 起動をしない。
    6. `--job-dir` には**絶対パス**を渡す（cwd 非依存）。

方式: 実 subprocess（合成のダミーではなく本物のプロセス）。対象スクリプトだけを
      検定用の軽いものへ差し替え、起動器そのものの振る舞いを見る。
"""
from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.subprocess_job_launcher import SubprocessJobLauncher

# 起動されたことと SIGTERM の受け取りが観測できる最小の子プロセス。
_CHILD = textwrap.dedent(
    """
    import argparse, os, signal, sys, time
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    args = ap.parse_args()
    d = os.path.abspath(args.job_dir)
    with open(os.path.join(d, "started.txt"), "w") as f:
        f.write("%s\\n%s\\n%s\\n" % (os.getpid(), os.getpgrp(), args.job_dir))
    def _bye(signum, frame):
        with open(os.path.join(d, "signal.txt"), "w") as f:
            f.write(str(signum))
        sys.exit(143)
    signal.signal(signal.SIGTERM, _bye)
    for _ in range(200):
        time.sleep(0.05)
    """
)

_EXIT_NOW = textwrap.dedent(
    """
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    ap.parse_args()
    sys.exit(int(__import__("os").environ.get("PROBE_RC", "0")))
    """
)


@pytest.fixture
def workspace(tmp_path: Path):
    """job_dir を採番するだけの最小の台帳役と、検定用スクリプト置き場。"""
    root = tmp_path / "data"
    root.mkdir()

    def job_dir_of(job_id: str) -> Path:
        d = root / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d.resolve()

    return job_dir_of, tmp_path


def _script(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _wait_for(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


# --- 1. 起動 ---------------------------------------------------------------

def test_起動すると子プロセスが動き出す(workspace) -> None:
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    # Act
    sut.launch("j1")
    try:
        # Assert
        assert _wait_for(job_dir_of("j1") / "started.txt")
    finally:
        sut.terminate("j1")


def test_job_dirは絶対パスで渡される(workspace) -> None:
    """cwd 非依存（起動場所が変わっても同じジョブディレクトリを指す）。"""
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    # Act
    sut.launch("j1")
    try:
        assert _wait_for(job_dir_of("j1") / "started.txt")
        passed = (job_dir_of("j1") / "started.txt").read_text().splitlines()[2]
        # Assert
        assert Path(passed).is_absolute()
        assert Path(passed) == job_dir_of("j1")
    finally:
        sut.terminate("j1")


def test_venv_pythonで起動する(workspace) -> None:
    """生 python 起動禁止（NFR-08）。sim core を動かしている実行系をそのまま使う。"""
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    # Act / Assert
    assert sut.python_executable == sys.executable


def test_子プロセスは同一プロセスグループで動く(workspace) -> None:
    """§12.7: setsid しない。新セッションを作ると serve.sh の PGID kill が届かない。"""
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    # Act
    sut.launch("j1")
    try:
        assert _wait_for(job_dir_of("j1") / "started.txt")
        pgid = int((job_dir_of("j1") / "started.txt").read_text().splitlines()[1])
        # Assert
        assert pgid == os.getpgrp(), "子プロセスが別のプロセスグループにいる（孤児化する）"
    finally:
        sut.terminate("j1")


# --- 2. 並列（§12.7）------------------------------------------------------

def test_複数ジョブが同時に走る(workspace) -> None:
    """§12.7「同時 1 本」案は棄却済み。直列化していれば 2 本目が始まらない。"""
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    # Act
    sut.launch("j1")
    sut.launch("j2")
    try:
        # Assert（両方が起動済み ＝ 1 本目の完了を待っていない）
        assert _wait_for(job_dir_of("j1") / "started.txt")
        assert _wait_for(job_dir_of("j2") / "started.txt")
        assert sut.poll("j1") is None
        assert sut.poll("j2") is None
    finally:
        sut.terminate("j1")
        sut.terminate("j2")


# --- 3. 取消（SIGTERM）----------------------------------------------------

def test_取消はSIGTERMを送る(workspace) -> None:
    # Arrange
    import signal

    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    sut.launch("j1")
    assert _wait_for(job_dir_of("j1") / "started.txt")
    # Act
    sut.terminate("j1")
    # Assert
    assert _wait_for(job_dir_of("j1") / "signal.txt")
    assert int((job_dir_of("j1") / "signal.txt").read_text()) == int(signal.SIGTERM)


def test_未知のジョブの取消は何も壊さない(workspace) -> None:
    """既に回収済み・未起動の識別子へ SIGTERM を送っても例外にしない。"""
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    # Act / Assert（例外が出れば失敗）
    sut.terminate("never-launched")


# --- 4. poll ---------------------------------------------------------------

def test_実行中はpollがNoneを返す(workspace) -> None:
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    sut.launch("j1")
    try:
        assert _wait_for(job_dir_of("j1") / "started.txt")
        # Act / Assert
        assert sut.poll("j1") is None
    finally:
        sut.terminate("j1")


@pytest.mark.parametrize("rc", [0, 1, 2])
def test_終了後は終了コードを返す(workspace, monkeypatch, rc: int) -> None:
    # Arrange
    monkeypatch.setenv("PROBE_RC", str(rc))
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, f"exit{rc}.py", _EXIT_NOW)
    )
    # Act
    sut.launch("j1")
    deadline = time.time() + 10
    while sut.poll("j1") is None and time.time() < deadline:
        time.sleep(0.02)
    # Assert
    assert sut.poll("j1") == rc


def test_未起動のジョブのpollはNone(workspace) -> None:
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    assert sut.poll("never-launched") is None


# --- 取消した子プロセスの回収（コードレビュー 🟡-1）------------------------

# 実測された壊れ方: SIGTERM を送っただけでは子は <defunct>（ゾンビ）として残る。
# 親が wait して初めてカーネルがプロセスエントリを解放する。sim core は常駐プロセス
# なので、回収しないと取消のたびにゾンビが積み上がる。
#
# TDD の誠実性に関する注記: 本節は実装（🟡-1）の後に追加した。Red は変異注入で確認して
# いる（`proc.wait(...)` を外すと `test_取消した子プロセスを回収する` が落ちることを実測）。

def test_取消した子プロセスを回収する(workspace) -> None:
    """SIGTERM 後に wait し、終了状態を回収すること（ゾンビを残さない）。"""
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    sut.launch("j-reap")
    assert _wait_for(job_dir_of("j-reap") / "started.txt")
    proc = sut._procs["j-reap"]
    # Act
    sut.terminate("j-reap")
    # Assert（returncode が確定＝wait 済み。未回収なら None のまま）
    assert proc.returncode is not None, "子プロセスを wait していない（ゾンビが残る）"


def test_回収した子は追跡表から外れる(workspace) -> None:
    """PID は OS に再利用される。回収済みを持ち続けると別プロセスを撃ちうる。"""
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    sut.launch("j-drop")
    assert _wait_for(job_dir_of("j-drop") / "started.txt")
    # Act
    sut.terminate("j-drop")
    # Assert
    assert "j-drop" not in sut._procs


def test_回収後の再取消は何も壊さない(workspace) -> None:
    """二重取消（UI の連打）で例外にならないこと。"""
    # Arrange
    job_dir_of, tmp_path = workspace
    sut = SubprocessJobLauncher(
        job_dir_of=job_dir_of, script=_script(tmp_path, "child.py", _CHILD)
    )
    sut.launch("j-twice")
    assert _wait_for(job_dir_of("j-twice") / "started.txt")
    sut.terminate("j-twice")
    # Act / Assert（2 回目は追跡表に無い＝何もしない）
    sut.terminate("j-twice")
