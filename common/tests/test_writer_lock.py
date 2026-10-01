"""``common.writer_lock`` — 単一書き手ロック（flock）の契約（ISSUE-530 / 先例 ISSUE-488）。

守る対象は「同一の木へ 2 本目の書き手を通さないこと」である。ライブ供給
（``tools/live_tick_watch.py``）は ISSUE-488 でこの錠を得ていたが、MT5 供給
（``tools/mt5_tick_watch.py``）には 1 つも無く、2026-09-23 に常駐が二重に起動して受信の
一次記録（ndjson ジャーナル）へ同時追記し、重複 1,225 行・ms 逆行 292 件を作った。

錠を **2 本目のファイルへ手で複製せず**、本モジュール 1 つを両者が使う形に置く（複製は必ず
取り残しを生む）。本モジュールは stdlib のみの中立核であり、どのアクター（tools / marketdata /
indigators）にも属さない。

固定するもの:
    1. 1 本目が持っている間、2 本目は即時に名前付きで拒まれる（宣言でなく flock で機械的に）。
    2. 保持者が死ねば（クローズでも SIGKILL でも）解放される＝錠はカーネルが持つ。
    3. 拒否の案内に**先行 PID**と**ロックのパス**が載る（運用者が次の一手を打てる）。
    4. 錠の名前（ファイル名）は呼出側が与える＝守る対象が別の木なら別の錠になる。
    5. ``takeover`` は先行へ SIGTERM を送って引き継ぐ（ライブ側 ISSUE-488 の挙動そのもの）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from common.writer_lock import WriterLockHeld, acquire_writer_lock

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 検定で使う錠の面（呼出側が与えるもの）。
_NAME = "writer_lock_selftest"
_FILENAME = "writer_lock_selftest.lock"
_HINT = "先行プロセスを停止してから起動してください。"


def _acquire(data_dir: Path, **kwargs):
    """検定対象の呼び出し（面の既定値を 1 箇所に置く）。"""
    return acquire_writer_lock(
        data_dir, filename=_FILENAME, name=_NAME, hint=_HINT, **kwargs
    )


def _child_holding(data_dir: Path, *, filename: str = _FILENAME) -> subprocess.Popen:
    """別プロセスに実ロックを握らせる（``locked`` を印字したら獲得済み）。"""
    child = subprocess.Popen(
        [sys.executable, "-c", (
            "import sys, time; sys.path.insert(0, sys.argv[1]);"
            "from common.writer_lock import acquire_writer_lock;"
            "h = acquire_writer_lock(sys.argv[2], filename=sys.argv[3],"
            " name='child', hint='');"
            "print('locked', flush=True); time.sleep(60)"
        ), str(_REPO_ROOT), str(data_dir), filename],
        stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "locked"
    return child


# =====================================================================
# 1. 2 本目が拒まれる
# =====================================================================
def test_a_second_writer_is_refused_while_the_first_holds(tmp_path: Path) -> None:
    """TC-01: 1 本目が持っている間、2 本目は獲得できない（二重起動の機械的禁止）。"""
    # Arrange
    first = _acquire(tmp_path)

    # Act / Assert
    try:
        with pytest.raises(WriterLockHeld):
            _acquire(tmp_path)
    finally:
        first.close()


def test_a_second_writer_is_refused_across_processes(tmp_path: Path) -> None:
    """TC-02: 別プロセスが保持していても拒む（flock は同一プロセス内の約束ではない）。"""
    # Arrange
    child = _child_holding(tmp_path)

    # Act / Assert
    try:
        with pytest.raises(WriterLockHeld):
            _acquire(tmp_path)
    finally:
        child.kill()


# =====================================================================
# 2. 保持者が死ねば解放される（錠はカーネルが持つ）
# =====================================================================
def test_the_lock_is_released_when_the_holder_closes(tmp_path: Path) -> None:
    """TC-03: クローズで解放される＝stale なロックファイルを残さない。"""
    # Arrange
    first = _acquire(tmp_path)
    first.close()

    # Act
    second = _acquire(tmp_path)

    # Assert
    try:
        assert (tmp_path / _FILENAME).read_text(encoding="utf-8").split()[0] == str(os.getpid())
    finally:
        second.close()


def test_the_lock_is_released_when_the_holder_process_dies(tmp_path: Path) -> None:
    """TC-04: SIGKILL で死んだ保持者の錠は自動解放される（後片付けの手順を運用に負わせない）。

    ロックファイルは残るが、錠そのものはカーネルが持つため次の書き手は獲得できる。
    """
    # Arrange
    child = _child_holding(tmp_path)
    child.kill()
    child.wait(timeout=10)

    # Act
    handle = _acquire(tmp_path)

    # Assert
    try:
        assert (tmp_path / _FILENAME).read_text(encoding="utf-8").split()[0] == str(os.getpid())
    finally:
        handle.close()


# =====================================================================
# 3. 案内の中身（先行 PID・ロックのパス）
# =====================================================================
def test_the_refusal_names_the_prior_pid_and_the_lock_path(tmp_path: Path) -> None:
    """TC-05: 拒否の案内は先行 PID とロックのパスを載せる（次の一手が打てる）。"""
    # Arrange
    child = _child_holding(tmp_path)

    # Act
    try:
        with pytest.raises(WriterLockHeld) as caught:
            _acquire(tmp_path)
    finally:
        child.kill()

    # Assert
    message = str(caught.value)
    assert str(child.pid) in message, f"先行 PID が案内に無い: {message}"
    assert str(tmp_path / _FILENAME) in message, f"ロックのパスが案内に無い: {message}"


def test_the_refusal_names_the_caller_and_its_hint(tmp_path: Path) -> None:
    """TC-06: 案内には呼出側の名前と次の一手（hint）が載る（錠が誰のものか分かる）。"""
    # Arrange
    first = _acquire(tmp_path)

    # Act
    try:
        with pytest.raises(WriterLockHeld) as caught:
            _acquire(tmp_path)
    finally:
        first.close()

    # Assert
    message = str(caught.value)
    assert _NAME in message, f"呼出側の名前が案内に無い: {message}"
    assert _HINT in message, f"次の一手が案内に無い: {message}"


def test_the_lock_file_records_the_holder_pid(tmp_path: Path) -> None:
    """TC-07: ロックファイルの先頭フィールドは保持者 PID（案内と takeover の宛先）。"""
    # Arrange / Act
    handle = _acquire(tmp_path)

    # Assert
    try:
        assert (tmp_path / _FILENAME).read_text(encoding="utf-8").split()[0] == str(os.getpid())
    finally:
        handle.close()


# =====================================================================
# 4. 錠の名前は呼出側が与える（別の木は別の錠）
# =====================================================================
def test_locks_with_different_names_do_not_refuse_each_other(tmp_path: Path) -> None:
    """TC-08: 別のファイル名の錠は互いに独立（ライブと MT5 は別の木・別の系列を守る）。"""
    # Arrange
    first = _acquire(tmp_path)

    # Act
    other = acquire_writer_lock(
        tmp_path, filename="other_writer.lock", name="other", hint=""
    )

    # Assert
    try:
        assert {p.name for p in tmp_path.iterdir()} == {_FILENAME, "other_writer.lock"}
    finally:
        other.close()
        first.close()


# =====================================================================
# 5. takeover（ライブ側 ISSUE-488 の挙動）
# =====================================================================
def test_takeover_stops_the_prior_holder_and_acquires(tmp_path: Path) -> None:
    """TC-09: ``takeover=True`` は先行へ SIGTERM を送り、解放を待って引き継ぐ。"""
    # Arrange
    child = _child_holding(tmp_path)

    # Act
    try:
        handle = _acquire(tmp_path, takeover=True)
        handle.close()

        # Assert
        assert child.wait(timeout=10) != 0        # SIGTERM で終了している
    finally:
        child.kill()                              # 既終了なら no-op


def test_takeover_gives_up_when_the_prior_holder_does_not_release(tmp_path: Path) -> None:
    """TC-10: 引き継げないときは待ち続けず、先行 PID を名指して諦める。"""
    # Arrange: SIGTERM を無視する保持者。
    child = subprocess.Popen(
        [sys.executable, "-c", (
            "import signal, sys, time; sys.path.insert(0, sys.argv[1]);"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            "from common.writer_lock import acquire_writer_lock;"
            "h = acquire_writer_lock(sys.argv[2], filename=sys.argv[3],"
            " name='child', hint='');"
            "print('locked', flush=True); time.sleep(60)"
        ), str(_REPO_ROOT), str(tmp_path), _FILENAME],
        stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "locked"

    # Act
    try:
        with pytest.raises(WriterLockHeld) as caught:
            _acquire(tmp_path, takeover=True, wait_seconds=0.5)
    finally:
        child.kill()

    # Assert
    assert str(child.pid) in str(caught.value), f"先行 PID が案内に無い: {caught.value}"
