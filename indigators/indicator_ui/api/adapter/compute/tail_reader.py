"""tail_reader — ファイル末尾から逆方向シークで最後の n_rows だけ読む（OOM 回避・D-2）。

1 分足原子（4.5M 行 / 284MB）を全読みすると OOM するため、末尾 n_rows（＋ヘッダ）だけを
ファイル末尾から逆方向シークで取得し ``set_index('date')`` した DataFrame を返す。全読みしない。

不変条件: ``read_tail(path, n)`` の結果は ``全読み.tail(n)`` と index/値で一致する
（``api/tests/test_tail_reader.py`` が oracle として固定）。
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

# 逆シークの読み取りブロック単位（末尾から遡る粒度）。
_BLOCK_SIZE = 64 * 1024


def _read_header(f) -> bytes:
    """ファイル先頭の 1 行（ヘッダ）を bytes で返す。"""
    f.seek(0)
    return f.readline()


def _read_last_lines(path: Path, n_rows: int) -> tuple[bytes, list[bytes]]:
    """末尾から逆シークしてヘッダと最後の n_rows データ行（bytes 行）を返す。

    全読みを避けるため、末尾から ``_BLOCK_SIZE`` ブロック単位で遡り、改行数が
    n_rows（＋ヘッダ確保のための余白）に達したら停止する。
    """
    path = Path(path)
    with open(path, "rb") as f:
        header = _read_header(f)
        f.seek(0, io.SEEK_END)
        file_size = f.tell()
        if file_size <= len(header):
            return header, []  # ヘッダのみ（データ 0 行）。

        buffer = b""
        # 末尾に必要な行数が揃うまでブロック単位で遡る（n_rows + 1 はヘッダ巻き込みの余白）。
        pos = file_size
        needed = n_rows + 1
        while pos > 0 and buffer.count(b"\n") <= needed:
            read_size = min(_BLOCK_SIZE, pos)
            pos -= read_size
            f.seek(pos)
            buffer = f.read(read_size) + buffer

    # 行へ分解（CR/LF を除去し空行を落とす）。csv.writer は \r\n 改行のため \n 分割後の
    # 各行末に \r が残りうる。strip() で正規化してから比較・採用する。
    header_norm = header.strip()
    all_lines = [ln.strip() for ln in buffer.split(b"\n")]
    all_lines = [ln for ln in all_lines if ln]
    # ヘッダ行がブロックに巻き込まれている場合は除去する（小ファイルで file 全体が読まれた時）。
    if all_lines and all_lines[0] == header_norm:
        all_lines = all_lines[1:]
    data_lines = all_lines[-n_rows:] if n_rows < len(all_lines) else all_lines
    return header_norm, data_lines


def read_tail(csv_path: Path, n_rows: int) -> pd.DataFrame:
    """CSV の末尾 n_rows だけを逆方向シークで読み ``set_index('date')`` した DataFrame を返す。

    全読みしない（末尾ブロックのみ遡る）。``n_rows`` が行数を超える場合は全件、ヘッダのみ・
    空ファイルは空 DataFrame を安全に返す。
    """
    header, data_lines = _read_last_lines(Path(csv_path), n_rows)
    if not data_lines:
        # ヘッダのみ: 列だけ持つ空 DataFrame を返す（後段の set_index も安全に通す）。
        cols = header.decode("utf-8").split(",")
        empty = pd.DataFrame(columns=cols)
        if "date" in empty.columns:
            empty = empty.set_index("date")
        return empty

    csv_bytes = header + b"\n" + b"\n".join(data_lines) + b"\n"
    df = pd.read_csv(io.BytesIO(csv_bytes), nrows=n_rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")
