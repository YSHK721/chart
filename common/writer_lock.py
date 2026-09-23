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


class WriterLockHeld(RuntimeError):
    """同一の木への書き手が既に居る（二重起動）。"""


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
    """ロックファイルの先頭フィールド（保持者 PID）を読む（壊れていれば None）。"""
    try:
        handle.seek(0)
        first = handle.read(64).split()
        return int(first[0]) if first else None
    except (ValueError, OSError):
        return None
