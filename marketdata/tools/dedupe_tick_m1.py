#!/usr/bin/env python3
"""dedupe_tick_m1 — 8 重連結した jp225_tick_m1.csv を date で重複除去する修復スクリプト（ISSUE-455）。

ISSUE-455 で jp225_tick_m1.csv は resume ガードの 1970 誤読により全履歴が毎分再追記され、
同一 date が最大 8 回連結した（3229 万行）。本スクリプトは date で重複除去（``keep="last"``＝
最終出現＝up/dn 有りブロックを採る・``marketdata/dataset.py`` の重複畳み規則と同一）し、date 昇順・
一様な列幅で書き直す。

列形の出どころ（ISSUE-511 段階 3・V-5）:
    **一様幅は対象ファイル自身のヘッダから読み、列順は台帳側の公開面
    （:func:`marketdata.csv_schema.header_for`）から導出する**（:func:`_header_columns`）。
    ヘッダの照合（:func:`_check_header`）は**データ行が在るときにだけ**行い、台帳が列順を
    定義していない列と、台帳と食い違う列順の 2 つを拒否する。畳む対象が無いファイル
    （空・ヘッダのみ・ヘッダの無い 1 行）は従来どおり何もしない。
    かつては 3 つの台帳導出値を手結合して幅を 8 に焼き込んでおり、実測（2026-09-17）で
    2 つの帰結があった: 9 列（spread 付き）の M1 は 1 行目で ``ValueError`` になり通らず、
    ヘッダが 6 列のファイルには存在しない ``up,dn`` が足されて全行の末尾へ空フィールドが
    書かれていた。手結合はリテラルではないため AST のリテラル走査では見えない。
    導出していることは ``marketdata/tests/test_dedupe_tick_m1_column_form.py`` が
    振る舞い（9 列往復・冪等・列を捏造しない・列順の食い違いを拒否）で固定する。

安全策（データ保全・CLAUDE.md）:
  - 元ファイルは必ず ``<name>.dup8x.bak`` へ ``os.rename`` で退避してから置換する（復元可能）。
    既存 ``.bak`` は上書きしない（別の退避を壊さない）。
  - 一意版は同一ディレクトリの一時ファイルへ書いてから ``os.replace`` で原子置換する
    （書き掛けの破損ファイルを確定パスに残さない）。
  - 冪等: 既に一意（重複 0）なら退避も置換もせず何もしない（再バックアップしない）。

メモリ有界（3229 万行対策）: 全行を DataFrame へ載せない。行単位で走査し、date ごとに最終出現行
だけを辞書に保持する（保持量 = 一意 date 数 ≒ 出力量で、それ以上には増えない）。date 文字列は
固定書式 ``YYYY-MM-DD HH:MM:SS`` のため辞書キーの昇順ソートは時刻順と一致する。

辞書が保持するのは **rstrip 済みの行文字列そのもの**であって、列へ分解した ``list`` ではない。
分解して保持すると、同じ行バイト長でも列 1 つにつき文字列オブジェクトが 1 つ増え、上の
「メモリ有界」が列数ぶん劣化する。実測（2026-09-17・``sys.getsizeof`` を容れ物と要素で深さ
合算・行バイト長 70・2000 行）: 分解して保持すると 6 列 463 B/行・9 列 583 B/行と列数で増えるのに
対し、行文字列の保持は 6 列・9 列とも 111 B/行で列数に不感である。行あたりの保持量が列数で
増えないことは ``marketdata/tests/test_dedupe_tick_m1_column_form.py`` が固定する。
列の取り出しは走査中に行わない（date は :meth:`str.partition`、列数は ``,`` の個数で数える）。

出力行の組み立て（:func:`_normalize`）は**書き出す行にだけ**発行する。重複で捨てる行ぶんを
組み立てない（作って捨てる計算を出さない）。発行量と出力量の差 0 は上記検定が固定する。
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

# 列順の出どころは marketdata.csv_schema の公開面（header_for・照合は _check_header）。
# 幅は対象ファイル自身のヘッダから読む（_header_columns）。
# keep-last（最終出現を採る）規則は marketdata.keep_last が唯一の実体（ISSUE-479 F-6）。
from marketdata import csv_schema, keep_last

_BACKUP_SUFFIX = ".dup8x.bak"


@dataclass(frozen=True)
class DedupeResult:
    """重複除去の結果（件数と実施内容）。"""

    total_rows_in: int          # 入力データ行数（ヘッダ除く）。
    unique_rows_out: int        # 一意 date 数（＝出力行数）。
    removed: int                # 捨てた重複行数（total_rows_in − unique_rows_out）。
    backup_path: "Path | None"  # 作成したバックアップのパス（未作成は None）。
    replaced: bool              # 本体を一意版へ置換したか。


def _split(raw: str) -> "list[str]":
    """1 行を改行を落として列へ分ける。"""
    return raw.rstrip("\n").rstrip("\r").split(",")


def _header_columns(path: Path) -> "list[str]":
    """対象ファイル自身のヘッダ行を、出力の一様列（幅と列名）として読む。

    幅の出どころは対象ファイルである。1 行目は常にヘッダとして扱う（本ツールの従来の規約）。
    """
    with open(path, "r", encoding="utf-8") as fh:
        return _split(fh.readline())


def _check_header(columns: "list[str]") -> None:
    """ヘッダが台帳と整合することを確かめる（整合しなければ ``ValueError`` で止める）。

    拒否する食い違いは 2 つある。どちらも「値が別の列名の下へ入る」ことを防ぐためである。

    1. **台帳が列順を定義していない列**を含む。:func:`marketdata.csv_schema.header_for` は
       未知列を末尾へ出現順で置くため、順序の照合だけでは素通りする。dedupe は重複行を
       畳む道具であって、台帳に無い列を推測して通す役ではない（通すと ISSUE-455 型の
       列ずれ検出をすり抜ける）。例外は食い違った列名を告げる。
    2. **台帳と食い違う列順**。不足列を末尾の空フィールドで埋める整形
       （:func:`_normalize`）は「欠けているのは末尾の列である」を前提にしており、その
       前提は列順が台帳と一致するときに成り立つ。並べ替えて辻褄を合わせると、値が別の
       列名の下へ移る。

    呼ぶのはデータ行が 1 行でも在るときに限る（:func:`_data_rows`）。整形する行が無ければ
    守るものが無く、畳む対象の無いファイルを停止させる理由も無い。
    """
    known = {c.lower() for c in csv_schema.VALUE_COLUMNS}
    unknown = [c for c in columns[1:] if str(c).lower() not in known]
    if unknown:
        raise ValueError(
            f"台帳が列順を定義していない列を含むヘッダです: {unknown}。"
            f"台帳の値列: {csv_schema.VALUE_COLUMNS}。"
            "台帳に無い列を推測して通すと列ずれの検出をすり抜けるため停止する"
            "（ISSUE-511 段階 3）。"
        )
    ordered = csv_schema.header_for(columns[1:])
    if columns != ordered:
        raise ValueError(
            f"列順が台帳と食い違うヘッダです: {columns}。台帳の順序: {ordered}。"
            "列を並べ替えて値を別の列の下へ移さないため停止する（ISSUE-511 段階 3）。"
        )


def _normalize(line: str, n_cols: int) -> str:
    """出力 1 行ぶんの文字列を組み立てる（不足列を空フィールドで埋めて一様幅に揃える）。

    up/dn を持たない旧 6 列行のように列の少ない行は末尾へ ``,`` を足し、出力の列数を
    ``n_cols`` に揃える（列数の乖離を残さない）。足りているなら保持した行文字列を
    そのまま返す（列へ分解し直さない＝分解の再実行を持ち込まない）。

    発行は書き出す行にだけ起きる。重複で捨てる行は :func:`_data_rows` が読んだ行文字列の
    まま辞書に入り、最終出現に選ばれた行だけがここを通る。
    """
    missing = n_cols - 1 - line.count(",")
    if missing > 0:
        return line + "," * missing
    return line


def _data_rows(path: Path, columns: "list[str]"):
    """データ行（ヘッダ・空行を除く）を ``(date_key, 行文字列)`` として **逐次**生成する。

    全行を一旦配列へ載せない（3229 万行をメモリに置かない）。列数がヘッダを超える行は
    列ずれの破損であり ``ValueError`` で止める（黙って捨てない）。

    行を列へ分解しない。date は先頭の ``,`` までを取り（:meth:`str.partition`）、列数は
    ``,`` の個数で数える。分解した列を辞書へ持たせると、保持量が列数ぶん増えて本モジュールの
    メモリ有界（冒頭）が劣化する。

    ヘッダは、最初のデータ行を取り出した後に 1 度だけ照合する（:func:`_check_header`）。
    データ行が 0 行なら照合せずに終わる。「1 度だけ」は行ごとに読み直す真偽フラグではなく、
    最初の 1 行を取り出す位置で表す。
    """
    n_cols = len(columns)
    with open(path, "r", encoding="utf-8") as fh:
        fh.readline()  # ヘッダを読み飛ばす。
        lines = (line for line in fh if line.strip())
        first = next(lines, None)
        if first is None:
            return  # 畳む対象が無い＝ヘッダを照合する対象も無い。
        _check_header(columns)
        for line in itertools.chain((first,), lines):
            row = line.rstrip("\n").rstrip("\r")
            if row.count(",") + 1 > n_cols:
                raise ValueError(
                    f"列数超過の破損行（{row.count(',') + 1} > {n_cols}）: {line!r}。"
                    "列がずれた行を黙って採用しないため停止する（ISSUE-455）。"
                )
            yield row.partition(",")[0], row


def _collect_last(path: Path, columns: "list[str]") -> Tuple[Dict[str, str], int]:
    """全データ行を走査し、date ごとの最終出現行（行文字列のまま）と総データ行数を返す。

    後勝ち（最終出現を採る）規則そのものは :mod:`marketdata.keep_last`（唯一の実体・
    ISSUE-479 F-6）へ委譲する。走査は 1 パスのままで、保持量は一意 date 数に留まる。
    """
    total = 0

    def _counted():
        nonlocal total
        for pair in _data_rows(path, columns):
            total += 1
            yield pair

    last: Dict[str, str] = keep_last.keep_last_by_key(_counted())
    return last, total


def _write_unique_atomic(dst: Path, last: Dict[str, str], columns: "list[str]") -> None:
    """一意行を date 昇順で ``dst`` へ原子的に書く（tmp→os.replace）。"""
    dst = Path(dst)
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent), prefix=dst.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
            out.write(",".join(columns) + "\n")
            for date in sorted(last):  # 固定書式ゆえ辞書順＝時刻昇順。
                out.write(_normalize(last[date], len(columns)) + "\n")
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def dedupe_file(path: "str | Path", *, dry_run: bool = False) -> DedupeResult:
    """``path`` の CSV を date で重複除去して原子置換する。件数を :class:`DedupeResult` で返す。

    ``dry_run=True`` は件数を数えるだけで一切書き込まない。既に一意（重複 0）なら退避も置換も
    せず何もしない（冪等・再バックアップしない）。
    """
    path = Path(path)
    columns = _header_columns(path)
    last, total = _collect_last(path, columns)
    unique = len(last)
    removed = total - unique

    if dry_run:
        return DedupeResult(total, unique, removed, backup_path=None, replaced=False)
    if removed == 0:
        # 既に一意 → 冪等 no-op（退避も置換もしない）。
        return DedupeResult(total, unique, 0, backup_path=None, replaced=False)

    backup = Path(str(path) + _BACKUP_SUFFIX)
    if backup.exists():
        raise FileExistsError(
            f"バックアップが既に存在します: {backup}。既存の退避を上書きしないため中断する"
            "（手動で退避先を確認・退避すること）。"
        )
    # 元を退避（rename）してから、保持済みの一意行を本体パスへ原子的に書き出す。
    os.rename(path, backup)
    try:
        _write_unique_atomic(path, last, columns)
    except BaseException:
        # 置換に失敗したら退避を元へ戻す（元ファイルを失わない）。
        if not path.exists():
            os.rename(backup, path)
        raise
    return DedupeResult(total, unique, removed, backup_path=backup, replaced=True)


def main(argv: "list[str] | None" = None) -> int:
    """CLI: ``python -m marketdata.tools.dedupe_tick_m1 <csv> [--dry-run]``。"""
    parser = argparse.ArgumentParser(
        description="jp225_tick_m1.csv の 8 重連結を date 一意へ修復する（ISSUE-455）。",
    )
    parser.add_argument("csv", help="対象 CSV パス（例: data/marketdata/jp225_tick_m1.csv）")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="件数のみ報告し書き込まない（前後行数を確認する）。",
    )
    args = parser.parse_args(argv)

    path = Path(args.csv)
    if not path.is_file():
        print(f"ファイルがありません: {path}", file=sys.stderr)
        return 2

    res = dedupe_file(path, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"[{mode}] {path}\n"
        f"  入力データ行数 : {res.total_rows_in:,}\n"
        f"  一意 date 数   : {res.unique_rows_out:,}\n"
        f"  重複除去       : {res.removed:,}\n"
        f"  バックアップ   : {res.backup_path}\n"
        f"  置換実施       : {res.replaced}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
