"""marketdata.tick_day_source — 日別ティックの **読み元** の解決と読取（ISSUE-512 段階 2）。

用語:
    日別ティックファイル
        ＝ 1 UTC 日ぶんのティックが在るファイル。確定 parquet か、確定前の受信ジャーナル
          （MT5 だけが持つ・:mod:`marketdata.mt5_ticks.journal`）のどちらか。
    読み元の解決
        ＝ 日ごとに「確定 parquet があればそれ、無ければ受信ジャーナル、どちらも無ければその日を
          飛ばす」を選ぶこと（:func:`day_tick_files`）。

なぜ要るか:
    MT5 は当日を追記専用ジャーナルで受け、1 UTC 日に 1 回 parquet へ確定する。読取側が parquet
    しか見ないと、当日だけ足内更新と市場プロファイルのティック系が空になる（ISSUE-512 実測）。
    Dukascopy の木はジャーナルを持たないため、本モジュールを通しても列挙は従来と完全に同じである。

ジャーナルは **追記された分だけ** 読む（読むたびに 1 日全体を変換し直さない）:
    ジャーナルは 5 秒ごとに追記される。読むたびに全行を変換すると、出力は正しいまま当日累積に
    比例した変換を毎回捨てる（実測 2026-09-13: 124,806 行で 0.54 秒／回、parquet は 0.025 秒）。
    そこで「どこまで読んだか（バイト位置）」と変換済みの frame をファイルごとに覚え、次の読取では
    その位置より後ろだけを変換して継ぎ足す（同日の実測: 5 秒ぶん 7 行で約 1.8 ミリ秒、結果は
    全行変換と一致）。ファイルが置き換わった（inode が変わった・縮んだ）ときは覚えた位置を捨てて
    先頭から読む。行の区切り（コミット判定）と行の形式は :mod:`marketdata.mt5_ticks.journal`
    が唯一の権威であり、本モジュールは同モジュールの読み口を呼ぶだけで形式を知らない。

記憶の寿命:
    覚えるのは「parquet がまだ無い日」のジャーナルだけである。:func:`day_tick_files` がその日を
    parquet で解決した時点で手放す（確定した日の記憶を残さない＝日数ぶん増えない）。

依存宣言（``marketdata/tests/test_module_dependency_declarations.py`` が AST で強制）:
    pandas / :mod:`marketdata.paths` / :mod:`marketdata.tick_tree`（木のレイアウトの唯一権威）/
    :mod:`marketdata.tick_m1`（列定義と集計規則の唯一源）/ :mod:`marketdata.mt5_ticks`
    （ジャーナルの形式とサーバ時刻 → UTC 変換の唯一源）。本モジュールは tick_tree・tick_m1・
    mt5_ticks の **上位** に置く。tick_m1 は mt5_ticks から import されるため、ここを tick_m1 の
    中に置くと循環になる。
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

from marketdata import tick_m1, tick_tree
from marketdata.mt5_ticks import ingest, journal
from marketdata.paths import DATA_DIR


def day_tick_files(
    start: Any, end: Any, *, symbol: str, data_dir: Any = DATA_DIR
) -> "List[Path]":
    """``[start, end]``（両端含む・日次）の日別ティックファイルを昇順で列挙する。

    日ごとに確定 parquet があればそれ、無ければ受信ジャーナル、どちらも無ければ飛ばす。
    日の進め方と端の扱いは :func:`marketdata.tick_tree.day_parquet_files` と同じ（ジャーナルを
    持たない木では同じ結果になる）。``symbol`` は必須（どの木かを既定値に委ねない）。
    """
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out: "List[Path]" = []
    d = s
    while d <= e:
        parquet = tick_tree.day_parquet_path(d, symbol=symbol, data_dir=data_dir)
        received = journal.journal_path(d, symbol=symbol, data_dir=data_dir)
        if parquet.is_file():
            _JOURNALS.forget(received)       # 確定した日の記憶は手放す
            out.append(parquet)
        elif received.is_file():
            out.append(received)
        d += pd.Timedelta(days=1)
    return out


def read_day_ticks(path: Any, columns: "Sequence[str]") -> pd.DataFrame:
    """日別ティックファイル 1 つを ``columns`` の列で読む（parquet と受信ジャーナルの両方）。

    返す frame は呼び出しごとに新しい（覚えている変換済み行を読み手が書き換えても汚れない）。
    """
    p = Path(path)
    if p.suffix == journal.JOURNAL_SUFFIX:
        return _JOURNALS.read(p)[list(columns)]
    return pd.read_parquet(p, columns=list(columns))


def forming_bar_from_ticks(
    start_unix: int, end_unix: int, *, symbol: str, price_basis: str, data_dir: Any = DATA_DIR
) -> "dict | None":
    """``[start_unix, end_unix)`` の実ティックから形成中バーを返す（読み元はジャーナルも含む）。

    契約は :func:`marketdata.tick_m1.forming_bar_from_ticks` と同じで、違いは読み元と、価格基準を
    **必須**で受けること（ISSUE-515: 既定 mid に委ねると、確定足が bid の ref で確定のたびに
    表示が跳ねる）。集計規則は :func:`marketdata.tick_m1.forming_bar_from_frame` の 1 箇所に
    委ねる（規則を 2 つに割らない）。読むのは窓と重なる日だけである。
    """
    s = pd.Timestamp(start_unix, unit="s")
    e = pd.Timestamp(end_unix, unit="s")
    if e <= s:
        return None
    # 窓は半開 [s, e) なので、e がちょうど日境界なら翌日は使わない（読んで捨てる日を作らない）。
    last = pd.Timestamp(int(end_unix) - 1, unit="s")
    files = day_tick_files(s.normalize(), last.normalize(), symbol=symbol, data_dir=data_dir)
    if not files:
        return None
    frames = [read_day_ticks(p, tick_m1.TICK_COLUMNS) for p in files]
    return tick_m1.forming_bar_from_frame(
        pd.concat(frames, ignore_index=True), start_unix, end_unix, price_basis=price_basis
    )


def ticks_since(
    cursor_ms: int, *, symbol: str, now_ms: "int | None" = None, data_dir: Any = DATA_DIR
) -> "List[Tuple[int, float, float]]":
    """``cursor_ms`` より厳密に後のティックを ``(UTC ms, bid, ask)`` の昇順で返す（ライブ供給口）。

    ライブ tick バッファ（indicator_ui の LiveTickBuffer）の供給口（引数 fetch_fn）として使う（ISSUE-515
    対策 2）。形は Dukascopy の供給口（:func:`marketdata.dukascopy_source.fetch_ticks_since`）と同じ。
    読むのはカーソルの日から ``now_ms`` の日までの日別ティックファイルだけで、各ファイルでは
    カーソルの位置を二分探索で求め、**後ろだけ** を組にする（毎回 1 日ぶんを組にして捨てない）。
    受信ジャーナルは追記分だけを変換する（:func:`read_day_ticks` と同じ記憶を使う）。読み終えた
    確定日（parquet）は、端を覚えてカーソルがそこに達していれば読み直さない（週末に 5 秒ごと
    前日を読み直さない）。
    """
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    lo = pd.Timestamp(int(cursor_ms), unit="ms").normalize()
    hi = pd.Timestamp(now, unit="ms").normalize()
    cut = pd.Timestamp(int(cursor_ms), unit="ms", tz="UTC")
    out: "List[Tuple[int, float, float]]" = []
    for path in day_tick_files(lo, hi, symbol=symbol, data_dir=data_dir):
        if path.suffix == journal.JOURNAL_SUFFIX:
            frame = _JOURNALS.read(path)
        else:
            if _FINALIZED_ENDS.reached(path, int(cursor_ms)):
                continue
            frame = pd.read_parquet(path, columns=list(tick_m1.TICK_COLUMNS))
            _FINALIZED_ENDS.remember(path, frame)
        tail = frame.iloc[int(frame["timestamp"].searchsorted(cut, side="right")):]
        if tail.empty:
            continue
        ms = tail["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ms]")
        out.extend(zip(
            ms.astype("int64").tolist(),
            tail["bidPrice"].astype("float64").tolist(),
            tail["askPrice"].astype("float64").tolist(),
        ))
    return out


class _FinalizedEnds:
    """確定 parquet ごとの「最後のティックの UTC ms」（ファイルの同一性つき・プロセス内）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ends: "Dict[Path, Tuple[Tuple[int, int], int]]" = {}

    @staticmethod
    def _identity(path: Path) -> "Tuple[int, int]":
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)

    def reached(self, path: Path, cursor_ms: int) -> bool:
        """カーソルがこのファイルの最後のティックに達しているか（ファイルが変わっていれば False）。"""
        with self._lock:
            entry = self._ends.get(path)
        return entry is not None and entry[0] == self._identity(path) and cursor_ms >= entry[1]

    def remember(self, path: Path, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        last = frame["timestamp"].iloc[-1]
        with self._lock:
            self._ends[path] = (self._identity(path), int(last.value // 1_000_000))

    def clear(self) -> None:
        with self._lock:
            self._ends.clear()


_FINALIZED_ENDS = _FinalizedEnds()


class _JournalMemory:
    """受信ジャーナルごとの「読んだバイト位置」と変換済み frame（プロセス内・スレッド安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: "Dict[Path, Tuple[Tuple[int, int], int, pd.DataFrame]]" = {}

    def read(self, path: Path) -> pd.DataFrame:
        """``path`` の全コミット済み行を frame で返す。変換するのは前回の位置より後ろだけ。"""
        st = os.stat(path)
        identity = (st.st_dev, st.st_ino)
        with self._lock:
            entry = self._entries.get(path)
            if entry is None or entry[0] != identity or st.st_size < entry[1]:
                entry = (identity, 0, ingest.rows_to_frame([]))   # 初回・置き換わり → 先頭から
            _, offset, frame = entry
            if st.st_size > offset:
                rows, offset = journal.read_committed_from(path, offset)
                if rows:
                    new = ingest.rows_to_frame(rows)
                    frame = new if frame.empty else pd.concat([frame, new], ignore_index=True)
            self._entries[path] = (identity, offset, frame)
            return frame

    def forget(self, path: Path) -> None:
        with self._lock:
            self._entries.pop(Path(path), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_JOURNALS = _JournalMemory()


def clear_journal_cache() -> None:
    """覚えているジャーナルと確定日の端を全部手放す（テスト・診断用）。"""
    _JOURNALS.clear()
    _FINALIZED_ENDS.clear()


def remembered_journals() -> int:
    """いま覚えているジャーナルの数（記憶が日数ぶん増えないことの検定・診断用）。"""
    return len(_JOURNALS)


__all__ = [
    "day_tick_files",
    "read_day_ticks",
    "forming_bar_from_ticks",
    "ticks_since",
    "clear_journal_cache",
    "remembered_journals",
]
