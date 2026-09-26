"""tail_reader — ファイル末尾から逆方向シークで最後の n_rows だけ読む（OOM 回避・D-2）。

1 分足原子（4.5M 行 / 284MB）を全読みすると OOM するため、末尾 n_rows（＋ヘッダ）だけを
ファイル末尾から逆方向シークで取得し ``set_index('date')`` した DataFrame を返す。全読みしない。

不変条件: ``read_tail(path, n)`` の結果は ``全読み.tail(n)`` と index/値で一致する。

行数で切る口（:func:`read_tail`）と**日時で切る口**（:func:`tail_bytes_since`）の 2 つを持つ。
後者は「この日時以降の行だけを、その開始バイト位置とともに」返す——末尾の一部を素材から書き直す
書き手（:func:`marketdata.tick_m1.heal_m1_days_for_series`）が、履歴（prefix）を読まず・触らずに
窓だけを突合できるようにするためである。逆シークの実体を 2 つに割らないため同じ所に置く。

依存方向（厳守）: pandas + 標準ライブラリのみに依存し、indicator_ui を逆 import しない
（marketdata の循環依存禁止・設計 §4）。:mod:`marketdata.rollup` が tail-read 用に再利用する。
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

# 逆シークの読み取りブロック単位（末尾から遡る粒度）。
_BLOCK_SIZE = 64 * 1024

#: 供給 tail の安全上限行数（**唯一の定義**・ISSUE-502 D-10）。
#:
#: 1 分足原子（:mod:`marketdata.dataset`）と上位足ロールアップ（:mod:`marketdata.rollup_store`）は
#: 同じ上限で末尾読みする。表示・計算は recentBars（1500 本）以内のため十分大きく、かつ 1m 全件
#: （4.5M 行）読みによる OOM を復活させない有限値である。かつては両所が同じ数値を各自に持ち、
#: 「同方式・同値」というコメントだけが人手同期を担っていた（値がずれても何も落ちなかった）。
#: 本定数を両所が参照することで、上限を変えるときに触る箇所が 1 つになる。
SERVING_TAIL_ROWS = 50_000


def _read_header(f) -> bytes:
    """ファイル先頭の 1 行（ヘッダ）を bytes で返す。"""
    f.seek(0)
    return f.readline()


def _line_starts(region: bytes) -> "list[int]":
    """``region`` の各データ行が始まる相対バイト位置を行順で返す（空行は行として数えない）。

    行境界で始まる領域を 1 回走査するだけで、位置を再計算しない（同じ走査を 2 度しない）。
    末尾に改行が無い行（torn 書込）も 1 行として数える。
    """
    starts: "list[int]" = []
    at = 0
    total = len(region)
    while at < total:
        nl = region.find(b"\n", at)
        end = total if nl == -1 else nl
        if region[at:end].strip():
            starts.append(at)
        if nl == -1:
            break
        at = nl + 1
    return starts


def _offset_of_first_line_since(region: bytes, since: pd.Timestamp) -> "int | None":
    """``region`` 内で ``since`` 以降の最初の行が始まる相対バイト位置（全行が以降なら ``None``）。

    ``region`` は行境界で始まる date 昇順の連続領域であることを前提とする（M1・ロールアップ CSV の
    不変条件）。昇順なので二分探索でよい——**行数に比例して日時を解釈しない**（窓の行数が増えても
    日時の解釈は log に収まる）。``since`` より前の行が 1 つも無ければ ``None`` を返す（呼出側は
    さらに遡る／領域の先頭を採る）。全行が ``since`` より前なら領域の長さを返す（該当行なし）。
    """
    starts = _line_starts(region)
    if not starts:
        return None
    def date_at(i: int) -> pd.Timestamp:
        end = region.find(b",", starts[i])
        field = region[starts[i]:None if end == -1 else end]
        return pd.Timestamp(field.decode("utf-8", "replace"))

    lo, hi = 0, len(starts)         # date_at(lo-1) < since <= date_at(hi) を保つ二分探索。
    while lo < hi:
        mid = (lo + hi) // 2
        if date_at(mid) < since:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None                 # 領域の全行が since 以降（さらに遡る余地がある）。
    return starts[lo] if lo < len(starts) else len(region)


def tail_bytes_since(csv_path: Path, since: "pd.Timestamp | str") -> "tuple[int, bytes]":
    """date 列が ``since`` 以降の**末尾の連続領域**を、その開始バイト位置とともに返す。

    返り値は「開始バイト位置, その位置以降のバイト列」の対である（バイト列は行境界で始まり、
    ヘッダを含まない）。該当行が 1 つも無ければ ``(ファイル長, b"")`` を返す
    ——呼出側はその位置へ追記すれば済む。

    読むのは ``since`` 以降の領域とその手前 1 ブロックだけである（全読みしない）。これは
    :func:`read_tail` と同じ逆シークで、切る基準が行数でなく日時であるだけの違いである。
    費用が**履歴の長さで増えない**ことは
    ``marketdata/tests/test_tick_m1_day_heal.py`` の CX-2 が履歴 2 点で固定する。

    前提（呼出側が保つ）: データ行は date 昇順であること（M1 CSV の不変条件・
    :func:`marketdata.tick_m1.append_m1_rows` が昇順で追記する）。降順・未整列のファイルへ
    使うと、境界の意味が失われる。
    """
    path = Path(csv_path)
    moment = pd.Timestamp(since)
    with open(path, "rb") as f:
        header = _read_header(f)
        lower = len(header)                      # ヘッダ行末の次バイト＝データ領域の先頭。
        f.seek(0, io.SEEK_END)
        size = f.tell()
        if size <= lower:
            return size, b""                     # ヘッダのみ（データ 0 行）。
        pos, buf = size, b""
        while pos > lower:
            step = min(_BLOCK_SIZE, pos - lower)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            if pos == lower:
                region, base = buf, pos          # データ領域の先頭まで遡った。
            else:
                nl = buf.find(b"\n")
                if nl == -1:
                    continue                     # 完全な行が 1 つも無い（さらに遡る）。
                region, base = buf[nl + 1:], pos + nl + 1
            rel = _offset_of_first_line_since(region, moment)
            if rel is not None:
                return base + rel, region[rel:]
            if pos == lower:
                return base, region              # 全データ行が since 以降。
    return size, b""


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

    # 列数整合の検査（ISSUE-455 再発防止・Fail-Stop）。データ行のフィールド数がヘッダ列数を
    # **超える**と、pd.read_csv は余剰フィールドを index へ回して列を丸ごとずらし、date 列に
    # 価格値（high）が入る。pd.to_datetime(価格) は数値をナノ秒と解釈して 1970-01-01 を返し、
    # 下流の resume ガード（index > last_date）が全履歴を再選択して毎分 8 重連結を生む。
    # 価格を黙って日付へ誤変換させないため、超過を検出したらここで止める（原因の除去）。
    #
    # 不足側（フィールド数 < ヘッダ列数）は raise しない: pandas は欠落する末尾列を NaN で
    # 埋めるため date/OHLC はずれず、非原子追記の torn 行（クラッシュ途中）も末尾に up/dn を
    # 持たない旧 6 列行も安全に読める。これらは NaN 検出（_is_healthy_m1_row）や loader 側の
    # 末尾不完全行破棄で従来どおり自己修復する経路に委ねる（過剰な Fail-Stop で正常な旧行を
    # 拒否しない）。危険なのは列がずれる超過側だけである。
    n_header = header.count(b",") + 1
    for ln in data_lines:
        n_fields = ln.count(b",") + 1
        if n_fields > n_header:
            raise ValueError(
                f"CSV 列数不整合（{csv_path}）: ヘッダ {n_header} 列に対しデータ行が "
                f"{n_fields} フィールド（例: {ln.decode('utf-8', 'replace')!r}）。"
                "価格を日付へ黙って誤変換させないため Fail-Stop する（ISSUE-455）。"
            )

    csv_bytes = header + b"\n" + b"\n".join(data_lines) + b"\n"
    df = pd.read_csv(io.BytesIO(csv_bytes), nrows=n_rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")
