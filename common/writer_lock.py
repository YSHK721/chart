"""単一書き手ロック（flock）— 同一の木へ 2 本目の書き手を通さない中立核。

守るのは「受信の一次記録と派生物へ同時に書く 2 本目を作らない」ことである。錠の実体は
**カーネルが持つ**（``fcntl.flock``）。保持者のプロセスが死ねば、SIGKILL でも電源断でも自動的に
解放されるため、PID ファイル方式のような stale な錠を運用に残さない（ロックファイル自体は
残るが、中身は診断表示用であり錠の在処ではない）。

所在の理由（ISSUE-530）:
    実体は ``tools/live_tick_watch.py`` にあり ISSUE-488 を根治していた（残存 watcher と
    serve.sh 起動分が同一 CSV へ非原子的に書き、1M の 8 月バーと 1D の 3 日ぶんが恒久欠落した）。
    一方 ``tools/mt5_tick_watch.py`` には防護が 1 つも無く、2026-09-23 に常駐が二重起動して
    ndjson ジャーナルへ同時追記し、重複 1,225 行・ms 逆行 292 件を作った。2 本目のファイルへ
    同じコードを手書きで複製すれば必ず取り残しが生まれるため、**両者が使う単一の定義**として
    中立核（stdlib のみ・どのアクターにも属さない）である本パッケージへ出した。
    ``common.watch_loop``（常駐の周期）と同じ位置付けである。

呼出側が与えるのは「面」だけである（規則は本モジュールが持つ）:
    filename : 錠の名前。**守る対象が別の木・別の系列なら別の名前**にする（ライブ供給と
               MT5 供給は互いを拒まない — 止めたいのは同じ木への 2 本目だけである）。
    name     : 拒否の案内に載せる呼出側の名前（錠が誰のものかを運用者へ伝える）。
    hint     : 拒否の案内の末尾に置く「次の一手」（引き継ぎ口を持つ側と持たない側で違う）。

取得の順序（呼出側の規律）:
    読むだけの起動時検査（列形照合など）を先に済ませ、**派生物へ書くどの経路よりも先**に
    獲得する。照合を錠より先に置くのは、``takeover`` が先行へ SIGTERM を送ってから自分も
    止まると、供給を止めたうえで誰も書かない状態を作るためである。
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("writer_lock")

#: ``takeover`` で先行プロセスへ SIGTERM を送った後、ロック解放を待つ上限秒。
DEFAULT_TAKEOVER_WAIT_SECONDS = 15.0

#: 錠ファイルから保持者 PID を読むときに読む先頭バイト数（**全体を読まない**）。
#:
#: 中身は「PID と ISO 時刻」の 1 行であり、在否の判定に要るのは先頭フィールドだけである。
#: 全体を読むと、追記途中の残骸がいくら伸びても費用が付いてくる。
_HOLDER_PREFIX_BYTES = 64


#: 在否の値（在＝錠が名指す PID が生きており、同一性の照合も一致した）。
WRITER_PRESENT = "present"

#: 在否の値（不在＝その PID のプロセスが居ない、または別のプログラムだった）。
WRITER_ABSENT = "absent"

#: 在否の値（判定不能＝錠ファイルが無い・読めない・PID が読み取れない）。**不在と別の値**で
#: あることが要点であり、居ないことと分からないことを同じ値にしない。
WRITER_UNDECIDABLE = "undecidable"


class WriterLockHeld(RuntimeError):
    """同一の木への書き手が既に居る（二重起動）。"""


def writer_presence(data_dir, *, filename: str, expect_cmdline_contains: str) -> str:
    """錠ファイルの残骸から書き手の在否を読む（**読み取りのみ**・ISSUE-526 段 2）。

    錠の実体はカーネルが持ち、保持者が死ねば自動解放される。残るのはロックファイルの中身
    （古い PID）だけである。この残骸がそのまま在否の台帳になるため、新しい state ファイルを
    作らない。書いている側が書式の権威なので、読み口も同じモジュールに置く。

    **flock を取らない。** ``LOCK_NB`` で取りに行くと、稼働中の書き手が握っている錠に弾かれて
    観測が失敗する。それは供給を守るために入れた錠（ISSUE-530）が、供給の監視を止める側へ
    回るということである。観測は先頭を読むだけで済む。

    同一性の照合（PID 再利用への防護）:
        PID は使い回される。錠が名指す PID のプロセスのコマンド行に
        ``expect_cmdline_contains`` が現れることを確かめる。照合しないと、たまたま同じ PID を
        得た無関係なプロセスを在と誤認する。**どのプログラムが書き手であるべきかは呼出側の
        面**である（錠の名前や案内文と同じ）。

    Returns:
        :data:`WRITER_PRESENT`（照合一致）／:data:`WRITER_ABSENT`（プロセス無し・または
        コマンド行が不一致）／:data:`WRITER_UNDECIDABLE`（錠ファイルが無い・読めない・
        PID が読み取れない）。不在と判定不能は**別の値**である。
    """
    path = Path(data_dir) / filename
    try:
        # buffering=0: 先頭バイト数の上限をそのまま読み取り 1 回にする（既定のバッファ越しだと
        #   受け取るのは先頭だけでも、カーネルからはバッファ 1 個分を読む）。
        with open(path, "rb", buffering=0) as handle:
            head = handle.read(_HOLDER_PREFIX_BYTES)
    except OSError:
        return WRITER_UNDECIDABLE
    pid = _holder_pid_of(head.decode("utf-8", "replace"))
    if pid is None:
        return WRITER_UNDECIDABLE
    cmdline = _holder_cmdline(pid)
    if cmdline is None:
        return WRITER_ABSENT
    return WRITER_PRESENT if expect_cmdline_contains in cmdline else WRITER_ABSENT


def _holder_pid_of(text: str) -> "int | None":
    """錠ファイルの先頭フィールド（保持者 PID）。空・数として読めなければ None。"""
    fields = text.split()
    if not fields:
        return None
    try:
        return int(fields[0])
    except ValueError:
        return None


def _holder_cmdline(pid: int) -> "str | None":
    """PID のプロセスのコマンド行（NUL 区切りを空白へ直す）。プロセスが居なければ None。"""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    return raw.replace(b"\0", b" ").decode("utf-8", "replace")


def acquire_writer_lock(
    data_dir,
    *,
    filename: str,
    name: str,
    hint: str,
    takeover: bool = False,
    wait_seconds: float = DEFAULT_TAKEOVER_WAIT_SECONDS,
):
    """``data_dir`` 配下の派生物に対する単一書き手ロックを獲得する。

    - 既定: 先行プロセスが居れば **即時失敗**（:class:`WriterLockHeld`・先行 PID と
      ロックのパスを名指す）。二重起動を宣言でなく flock で機械的に禁じる。
    - ``takeover=True``: 先行プロセスへ SIGTERM を送り、解放を待って引き継ぐ
      （正規の起動経路が、出所不明の残存プロセスを確実に退去させるための口）。

    Returns:
        獲得済みロックのファイルオブジェクト。**保護したい区間のあいだ参照を保持すること**
        （閉じる／プロセスが死ぬと解放される）。
    """
    import fcntl

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / filename
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = _lock_holder_pid(handle)
        if not takeover:
            handle.close()
            raise WriterLockHeld(
                f"{name} は既に稼働中です（PID {holder if holder else '不明'}・"
                f"lock={path}）。{hint}"
            )
        if holder:
            logger.warning("先行の %s (PID %s) を停止して引き継ぎます。", name, holder)
            try:
                os.kill(holder, 15)   # SIGTERM
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise WriterLockHeld(
                        f"先行プロセス（PID {holder if holder else '不明'}）が"
                        f" {wait_seconds:.0f} 秒以内にロックを解放しませんでした。"
                    )
                time.sleep(0.2)
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()} {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
    handle.flush()
    return handle


def _lock_holder_pid(handle) -> "int | None":
    """開いているロックファイルから保持者 PID を読む（壊れていれば None）。

    先頭フィールドが保持者 PID であるという規則は :func:`_holder_pid_of` が唯一持つ
    （拒否の案内・``takeover`` の宛先・在否の観測の 3 者が同じ規則を見る）。
    """
    try:
        handle.seek(0)
        return _holder_pid_of(handle.read(_HOLDER_PREFIX_BYTES))
    except OSError:
        return None
