#!/usr/bin/env python3
"""dedupe_tick_m1 — 8 重連結した ``jp225_tick_m1.csv`` を date で重複除去する修復スクリプト（ISSUE-455）。

ISSUE-455 で ``jp225_tick_m1.csv`` は resume ガードの 1970 誤読により全履歴が毎分再追記され、
同一 date が最大 8 回連結した（3229 万行）。本スクリプトは date で重複除去（``keep="last"``＝
最終出現＝up/dn 有りブロックを採る・``marketdata/dataset.py`` の重複畳み規則と同一）し、date 昇順・
一様 8 列ヘッダ（``date,open,high,low,close,volume,up,dn``）で書き直す。

安全策（データ保全・CLAUDE.md）:
  - 元ファイルは必ず ``<name>.dup8x.bak`` へ ``os.rename`` で退避してから置換する（復元可能）。
    既存 ``.bak`` は上書きしない（別の退避を壊さない）。
  - 一意版は同一ディレクトリの一時ファイルへ書いてから ``os.replace`` で原子置換する
    （書き掛けの破損ファイルを確定パスに残さない）。
  - 冪等: 既に一意（重複 0）なら退避も置換もせず何もしない（再バックアップしない）。

メモリ有界（3229 万行対策）: 全行を DataFrame へ載せない。行単位で走査し、date ごとに最終出現行
だけを辞書に保持する（保持量 = 一意 date 数 ≒ 出力量で、それ以上には増えない）。date 文字列は
固定書式 ``YYYY-MM-DD HH:MM:SS`` のため辞書キーの昇順ソートは時刻順と一致する。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

# 出力の一様スキーマ（marketdata.csv_schema と同一列順の単一定義を参照）。
from marketdata import csv_schema

HEADER_COLUMNS = [csv_schema.HEADER[0], *csv_schema.OHLCV_COLUMNS, *csv_schema.UPDOWN_COLUMNS]
HEADER_LINE = ",".join(HEADER_COLUMNS)  # "date,open,high,low,close,volume,up,dn"
_N_COLS = len(HEADER_COLUMNS)  # 8
_BACKUP_SUFFIX = ".dup8x.bak"


@dataclass(frozen=True)
class DedupeResult:
    """重複除去の結果（件数と実施内容）。"""

    total_rows_in: int          # 入力データ行数（ヘッダ除く）。
    unique_rows_out: int        # 一意 date 数（＝出力行数）。
    removed: int                # 捨てた重複行数（total_rows_in − unique_rows_out）。
    backup_path: "Path | None"  # 作成したバックアップのパス（未作成は None）。
    replaced: bool              # 本体を一意版へ置換したか。


def _normalize(raw: str) -> Tuple[str, str]:
    """1 データ行を ``(date_key, 8 列へ正規化した行文字列)`` に変換する。

    up/dn を持たない旧 6 列行は末尾を空フィールドで埋め、出力を一様 8 列に揃える（列数の乖離を
    残さない）。列数がヘッダを超える行は列ずれの破損であり ``ValueError`` で止める（黙って捨てない）。
    """
    fields = raw.rstrip("\n").rstrip("\r").split(",")
    if len(fields) > _N_COLS:
        raise ValueError(
            f"列数超過の破損行（{len(fields)} > {_N_COLS}）: {raw!r}。"
            "列がずれた行を黙って採用しないため停止する（ISSUE-455）。"
        )
    date = fields[0]
    if len(fields) < _N_COLS:
        fields = fields + [""] * (_N_COLS - len(fields))
    return date, ",".join(fields)


def _collect_last(path: Path) -> Tuple[Dict[str, str], int]:
    """全データ行を走査し、date ごとの最終出現行（正規化済み）と総データ行数を返す。"""
    last: Dict[str, str] = {}
    total = 0
    with open(path, "r", encoding="utf-8") as fh:
        fh.readline()  # ヘッダを読み飛ばす。
        for line in fh:
            if not line.strip():
                continue
            total += 1
            date, norm = _normalize(line)
            last[date] = norm  # 後勝ち＝最終出現（keep="last"）。
    return last, total


def _write_unique_atomic(dst: Path, last: Dict[str, str]) -> None:
    """一意行を date 昇順で ``dst`` へ原子的に書く（tmp→os.replace）。"""
    dst = Path(dst)
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent), prefix=dst.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
            out.write(HEADER_LINE + "\n")
            for date in sorted(last):  # 固定書式ゆえ辞書順＝時刻昇順。
                out.write(last[date] + "\n")
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
    last, total = _collect_last(path)
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
        _write_unique_atomic(path, last)
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
