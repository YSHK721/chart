"""受信ジャーナル（追記専用）と日次確定（adapter E）。

記憶域を 2 段に分ける理由（P4 棄却の根拠）:
    ``tools/live_tick_watch.py:392-399`` は行が届くたびに当日全量を concat して再直列化する。
    受信が進むほど 1 回の書込が重くなり、**当日累積に比例した無駄**を生む。本モジュールは
    受信を追記専用ジャーナル（``O(新着)``）に、確定を 1 UTC 日 1 回の parquet 化に分ける。
    どちらも「作ってから捨てる」計算を持たない（計算量検定 CX-b / CX-d）。

行の commit マーカーは改行である:
    追記の途中でプロセスが落ちると末尾に書き掛けの行が残る。改行で終わっていない行は
    「まだ commit されていない」と見なして読み手が捨てる（検定 E-9）。末尾**以外**の破損は
    捨てない。穴の空いた台帳を黙って作らないためである。

ジャーナルを消さない理由:
    parquet は集計後の姿であり、受信の一次記録ではない。確定後もジャーナルを残すことで
    「取れていないのか、集計で落ちたのか」を後から区別できる。

パスの権威:
    ``<木>/<token>_ticks.ndjson`` は :func:`marketdata.tick_m1.day_parquet_path` の拡張子
    差し替えで得る（派生 1 箇所・自前レイアウト禁止・検定 M-2）。

依存宣言: pandas / :mod:`marketdata.tick_m1` / :mod:`marketdata.mt5_ticks` 下位。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, List, Sequence, Tuple

import pandas as pd

from marketdata import tick_m1
from marketdata.mt5_ticks import ingest
from marketdata.mt5_ticks.port import Mt5SupplyError

Row = Tuple[int, float, float]

#: ジャーナルの拡張子（1 行 = 1 ティックの NDJSON）。
JOURNAL_SUFFIX = ".ndjson"


def journal_path(day: Any, *, symbol: str, data_dir: Any) -> Path:
    """``day`` の受信ジャーナルの正準パス（tick 木の権威から派生する）。"""
    return tick_m1.day_parquet_path(day, symbol=symbol, data_dir=data_dir).with_suffix(
        JOURNAL_SUFFIX
    )


def serialize_rows(rows: "Sequence[Row]") -> str:
    """行を NDJSON へ（float は最短往復表現＝値が丸まらない）。

    直列化を独立した関数に切り出してあるのは、計算量検定（CX-d）が
    「**何行を直列化したか**」を Test Spy で数えられるようにするためである。書込の重さは
    ここを通る行数だけで決まり、当日の累積は 1 行も通らない。
    """
    return "".join(
        json.dumps([int(ms), float(bid), float(ask)], separators=(",", ":")) + "\n"
        for ms, bid, ask in rows
    )


def append(day: Any, rows: "Sequence[Row]", *, symbol: str, data_dir: Any) -> int:
    """``rows`` をジャーナル末尾へ**1 回の write** で追記し、書いた行数を返す。

    既存分は読まない・書き戻さない（``O(新着)``）。新着 0 なら file も作らない。
    """
    rows = list(rows)
    if not rows:
        return 0
    path = journal_path(day, symbol=symbol, data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = serialize_rows(rows)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    return len(rows)


def _parse_line(line: str, *, path: Path, number: int) -> Row:
    try:
        value = json.loads(line)
        ms, bid, ask = value
        return (int(ms), float(bid), float(ask))
    except (ValueError, TypeError) as exc:
        raise Mt5SupplyError(
            f"ジャーナルの {number} 行目が壊れています（{path}）: {line!r}。"
            " 末尾以外の破損は捨てない（穴の空いた台帳を作らないため）。"
        ) from exc


def read_rows(day: Any, *, symbol: str, data_dir: Any) -> "List[Row]":
    """``day`` のジャーナル全行を読む。**改行で終わっていない末尾行は捨てる**（E-9）。"""
    path = journal_path(day, symbol=symbol, data_dir=data_dir)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    committed = lines[:-1]  # 末尾要素は最後の改行の後ろ＝空文字か torn 行。
    return [
        _parse_line(line, path=path, number=i)
        for i, line in enumerate(committed, 1)
        if line
    ]


def tail_rows(day: Any, *, symbol: str, data_dir: Any) -> "List[Row]":
    """カーソル復元の入力（末尾付近の行）を返す。ジャーナルが正である唯一の読み口。"""
    return read_rows(day, symbol=symbol, data_dir=data_dir)


def _write_parquet_atomically(frame: pd.DataFrame, path: Path) -> None:
    """tmp→``os.replace`` で確定パスを「完全な新ファイル」か「旧ファイル」に限定する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        frame.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def finalize(day: Any, *, symbol: str, data_dir: Any) -> str:
    """``day`` のジャーナルを日別 parquet へ確定する（**1 UTC 日 1 回**）。

    戻り値は ``"written"`` / ``"unchanged"`` / ``"empty"``。既存 parquet と内容が一致する
    場合は**書かない**（``"unchanged"``）。行が 1 つも無い日は parquet を作らず ``.empty``
    マーカーだけを置く（``"empty"``）。

    呼び出し前提: 本関数は「走査済みの日」に対してのみ呼ぶ。走査していない日に呼ぶと
    ティックが在るのに ``.empty`` を置くことになる。この判断は UC-02 FinalizeDay が持つ。
    """
    rows = read_rows(day, symbol=symbol, data_dir=data_dir)
    parquet = tick_m1.day_parquet_path(day, symbol=symbol, data_dir=data_dir)

    if not rows:
        marker = tick_m1.day_empty_marker_path(day, symbol=symbol, data_dir=data_dir)
        marker.parent.mkdir(parents=True, exist_ok=True)
        if not marker.is_file():
            marker.write_text("", encoding="utf-8")
        return "empty"

    frame = ingest.rows_to_frame(rows)
    if parquet.is_file():
        existing = pd.read_parquet(parquet, columns=tick_m1._TICK_COLUMNS)
        if existing.reset_index(drop=True).equals(frame.reset_index(drop=True)):
            return "unchanged"
    _write_parquet_atomically(frame, parquet)
    return "written"
