"""``common.writer_lock`` — 書き手の在否を**読むだけ**で言う口の契約（ISSUE-526 段 2）。

錠の実体はカーネルが持つ（:func:`common.writer_lock.acquire_writer_lock`）。保持者が死ねば
錠は自動解放され、**ロックファイルの中身だけが古い PID として残る**。この残骸がそのまま
「誰が書き手だったか」の台帳になるため、在否の読み口は新しい state ファイルを作らず、
書いている側と同じ場所（本モジュール）に置く。

固定するもの:
    1. 錠ファイルの PID が生きており、同一性の照合も一致すれば **在**。
    2. その PID のプロセスが居ない／別のプログラムだったなら **不在**（PID 再利用への防護）。
    3. 錠ファイルが無い・壊れているなら **判定不能**。**不在とは別の値**である
       （居ないことと、分からないことを同じ値にしない）。
    4. 観測は **flock を 1 度も取らない**。取りに行くと、稼働中の書き手が握っている錠に
       弾かれて観測が失敗し、逆に供給を止める側へ回る（ISSUE-530 で入れた錠の裏目）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from common.writer_lock import (
    WRITER_ABSENT,
    WRITER_PRESENT,
    WRITER_UNDECIDABLE,
    writer_presence,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 検定で使う錠の面（呼出側が与えるもの）。
_FILENAME = "writer_presence_selftest.lock"

#: 子プロセスの引数へ載せる目印。照合はプロセスのコマンド行に**この語が現れるか**で行う。
_MARKER = "writer_presence_selftest_marker"

#: どのプロセスのコマンド行にも現れない語（照合が実際に効いていることを示すため）。
_FOREIGN_MARKER = "writer_presence_selftest_no_such_program"


def _write_lock_file(data_dir: Path, pid: int) -> None:
    """錠ファイルを書き手と同じ書式（PID と ISO 時刻）で置く（残骸の再現）。"""
    (data_dir / _FILENAME).write_text(
        f"{pid} 2026-09-25T03:30:00.000000+00:00\n", encoding="utf-8")


def _presence(data_dir: Path, *, expect: str = _MARKER) -> str:
    """検定対象の呼び出し（面の既定値を 1 箇所に置く）。"""
    return writer_presence(
        data_dir, filename=_FILENAME, expect_cmdline_contains=expect)


def _live_child() -> subprocess.Popen:
    """目印をコマンド行に持つ生きたプロセスを 1 本起こす（起動を印字で待ち合わせる）。"""
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; print('up', flush=True); time.sleep(60)", _MARKER],
        stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "up"
    return child


def _child_holding_the_lock(data_dir: Path) -> subprocess.Popen:
    """目印を持つ子に**本物の錠**を握らせる（``locked`` を印字したら獲得済み）。"""
    child = subprocess.Popen(
        [sys.executable, "-c", (
            "import sys, time; sys.path.insert(0, sys.argv[1]);"
            "from common.writer_lock import acquire_writer_lock;"
            "h = acquire_writer_lock(sys.argv[2], filename=sys.argv[3],"
            " name='child', hint='');"
            "print('locked', flush=True); time.sleep(60)"
        ), str(_REPO_ROOT), str(data_dir), _FILENAME, _MARKER],
        stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "locked"
    return child


def _child_takes_the_exclusive_lock(path: Path) -> bool:
    """別プロセスが ``path`` の排他ロックを**取れたか**を返す（取れなければ誰かが握っている）。"""
    done = subprocess.run(
        [sys.executable, "-c", (
            "import fcntl, sys;"
            "h = open(sys.argv[1], 'a+');"
            "fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)"
        ), str(path)],
        capture_output=True, text=True,
    )
    return done.returncode == 0


# =====================================================================
# 1. 在（照合一致）
# =====================================================================
def test_the_writer_is_present_when_the_lock_names_a_live_matching_process(
    tmp_path: Path,
) -> None:
    """TC-01: 錠ファイルの PID が生きており、コマンド行の照合も一致すれば在。"""
    # Arrange
    child = _live_child()
    _write_lock_file(tmp_path, child.pid)

    # Act
    try:
        verdict = _presence(tmp_path)
    finally:
        child.kill()
        child.wait(timeout=10)

    # Assert
    assert verdict == WRITER_PRESENT


# =====================================================================
# 2. 不在（プロセス無し・または照合不一致）
# =====================================================================
def test_the_writer_is_absent_when_the_pid_in_the_lock_has_no_process(
    tmp_path: Path,
) -> None:
    """TC-02: 死んだ保持者の PID が残っただけの錠は不在（判定不能ではない）。

    PID が別のプロセスへ再利用されていても、目印を持たないので不在になる。
    """
    # Arrange
    child = _live_child()
    child.kill()
    child.wait(timeout=10)
    _write_lock_file(tmp_path, child.pid)

    # Act
    verdict = _presence(tmp_path)

    # Assert
    assert verdict == WRITER_ABSENT


def test_the_writer_is_absent_when_the_pid_belongs_to_another_program(
    tmp_path: Path,
) -> None:
    """TC-03: 生きた PID でもコマンド行が食い違えば不在（PID 再利用への防護）。

    照合を省くと、たまたま同じ PID を得た無関係なプロセスを在と誤認する。
    """
    # Arrange
    _write_lock_file(tmp_path, os.getpid())

    # Act
    verdict = _presence(tmp_path, expect=_FOREIGN_MARKER)

    # Assert
    assert verdict == WRITER_ABSENT


# =====================================================================
# 3. 判定不能（錠ファイルが無い・読めない）
# =====================================================================
def test_presence_is_undecidable_when_there_is_no_lock_file(tmp_path: Path) -> None:
    """TC-04: 錠ファイルが無ければ判定不能（不在と断じない）。"""
    # Arrange / Act
    verdict = _presence(tmp_path)

    # Assert
    assert verdict == WRITER_UNDECIDABLE


def test_presence_is_undecidable_when_the_lock_file_is_empty(tmp_path: Path) -> None:
    """TC-05: 中身が空（獲得直後の truncate 途中など）なら判定不能。"""
    # Arrange
    (tmp_path / _FILENAME).write_bytes(b"")

    # Act
    verdict = _presence(tmp_path)

    # Assert
    assert verdict == WRITER_UNDECIDABLE


def test_presence_is_undecidable_when_the_first_field_is_not_a_pid(
    tmp_path: Path,
) -> None:
    """TC-06: 先頭フィールドが PID として読めない（壊れた中身）なら判定不能。"""
    # Arrange
    (tmp_path / _FILENAME).write_bytes(b"\0\0not-a-pid 2026-09-25\n")

    # Act
    verdict = _presence(tmp_path)

    # Assert
    assert verdict == WRITER_UNDECIDABLE


def test_absent_and_undecidable_are_different_values() -> None:
    """TC-07: 不在と判定不能は別の値（同じ値にすると「分からない」が消える）。"""
    # Arrange / Act / Assert
    assert len({WRITER_PRESENT, WRITER_ABSENT, WRITER_UNDECIDABLE}) == 3


# =====================================================================
# 4. 観測は錠を握らない（ISSUE-530 の裏目を機械的に禁じる）
# =====================================================================
def test_another_process_can_take_the_exclusive_lock_while_we_are_observing(
    tmp_path: Path, monkeypatch,
) -> None:
    """TC-08: 観測中でも、他プロセスが排他ロックを取得できる（観測は錠を握らない）。

    「観測中」を決定的に作るため、読み口が開いたファイルハンドルへ介入し、**開いている
    最中**と**読んだ直後**の 2 点で別プロセスに排他ロックを取らせる。取れなければ観測が
    錠を握っている。
    """
    # Arrange
    from common import writer_lock

    lock_path = tmp_path / _FILENAME
    _write_lock_file(tmp_path, os.getpid())
    taken: "list[bool]" = []
    real_open = open

    class _ObservedFile:
        def __init__(self, handle) -> None:
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            taken.append(_child_takes_the_exclusive_lock(lock_path))
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

        def read(self, *args):
            data = self._handle.read(*args)
            taken.append(_child_takes_the_exclusive_lock(lock_path))
            return data

    def observed_open(path, mode="r", *args, **kwargs):
        return _ObservedFile(real_open(path, mode, *args, **kwargs))

    monkeypatch.setattr(writer_lock, "open", observed_open, raising=False)

    # Act
    _presence(tmp_path, expect=_FOREIGN_MARKER)

    # Assert
    assert taken and all(taken), f"観測中に排他ロックを取れませんでした: {taken}"


def test_observing_succeeds_while_the_real_writer_holds_the_lock(
    tmp_path: Path,
) -> None:
    """TC-09: 稼働中の書き手が錠を握っている間も、観測は在と答える（拒まれない）。

    読み口が ``LOCK_NB`` で取りに行くと、ここで例外になり、観測が供給を止める側へ回る。
    """
    # Arrange
    child = _child_holding_the_lock(tmp_path)

    # Act
    try:
        verdict = _presence(tmp_path)
    finally:
        child.kill()
        child.wait(timeout=10)

    # Assert
    assert verdict == WRITER_PRESENT
