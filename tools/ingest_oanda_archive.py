"""OANDA 月別アーカイブ（月 zip）を tick 木へ取り込む CLI（合成点・規則を持たない）。

``tools/download_oanda_ticks.py`` が**取得**だけを行うのに対し、本スクリプトは**取り込み**
だけを行う（取得と解釈の分離）。解釈の規則——月の識別・並べ替え・サーバ時刻ラベルの
UTC 変換・UTC 日分割・月跨ぎ日の結合・列と dtype・日別 parquet のパス・原子置換——は
すべて :mod:`marketdata.mt5_ticks.archive_ingest` とその委譲先が持つ。ここに規則を書けば
それは合成点ではなくライブラリの仕事である（``tools/__init__.py`` の宣言）。

既定の書き込み先トークンは :func:`marketdata.mt5_ticks.ingest.token_for` が
:data:`DEFAULT_SYMBOL` と :data:`DEFAULT_SERVER` から**組み立てる**値であり、既存
Dukascopy 木の銘柄とは別トークンになる。綴りを書き写さないのは、片方だけ直った
瞬間に別ディレクトリへ書き始めるからである（``test_ingest_oanda_archive.py`` が
リテラルの混入を機械的に禁じる）。

既存の日別 parquet は**上書きしない**。ただし読み飛ばしてよいのは既存と入力が同じ日である
ときだけで、食い違えば中止する（規則は :func:`archive_ingest.ingest_months` が持つ）。
``--dry-run`` は 1 バイトも書かず、書くはずだった日数・行数だけを報告する。

走査範囲の端にある UTC 日は書かれない（先頭は切り落とし・末尾は持ち越し）。理由は
:func:`marketdata.mt5_ticks.archive_ingest.ingest_months` の docstring にある。報告には
どちらも行数付きで出る（黙って捨てない）。

``--symbol-token`` は :data:`marketdata.mt5_ticks.ingest.TOKEN_SEPARATOR` を**含む**必要が
ある。銘柄だけのトークンは既存 Dukascopy 木を指すため、コマンド行の段階で拒否する。

終了コード: ``0`` 正常 / ``2`` 入力・指定の誤り、Fail-Stop、行方の説明がつかない行あり。

使い方::

    python -m tools.ingest_oanda_archive --dry-run
    python -m tools.ingest_oanda_archive --months 2020-05:2020-12
    python -m tools.ingest_oanda_archive --months 2020-05      # 単月
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from marketdata.mt5_ticks import archive_ingest, ingest
from marketdata.mt5_ticks.port import Mt5SupplyError

LOG = logging.getLogger("ingest_oanda_archive")

#: 供給元（MT5 サーバ名）。銘柄仕様の供給連鎖と同じ綴りを 1 箇所だけ持つ。
DEFAULT_SERVER = "OANDA-Japan-MT5-Live"

#: 既定の銘柄。
DEFAULT_SYMBOL = "JP225"

#: 既定の書き込み先トークン（**組み立てて**得る・書き写さない）。
DEFAULT_SYMBOL_TOKEN = ingest.token_for(DEFAULT_SYMBOL, DEFAULT_SERVER)

#: 月の範囲指定の区切り（``2020-05:2020-12``・片側省略可）。
RANGE_SEPARATOR = ":"


def select_months(
    src_dir: Path, months: "Optional[str]" = None
) -> "List[Path]":
    """``src_dir`` の月 zip から、範囲 ``FROM:TO``（両端含む）に入るものを選ぶ。

    月の読み取りは :func:`archive_ingest.month_key` が権威である（CLI では解釈しない）。
    比較が文字列のままで成立するのは ``YYYY-MM`` が辞書順 = 時系列順だからである。

    区切りの無い ``2020-05`` は**単月**として扱う（``2020-05:`` と同義にしない）。
    「開始だけ」と読むと、1 か月だけ入れるつもりの指示が以降の全月を取り込む。
    """
    text = months or ""
    if text and RANGE_SEPARATOR not in text:
        start = end = text
    else:
        start, _, end = text.partition(RANGE_SEPARATOR)
    selected = []
    for path in sorted(src_dir.glob("*.zip")):
        key = archive_ingest.month_key(path)
        if start and key < start:
            continue
        if end and key > end:
            continue
        selected.append(path)
    return selected


def run(args: argparse.Namespace) -> int:
    src_dir = Path(args.src_dir)
    if not src_dir.is_dir():
        LOG.error("入力ディレクトリがありません: %s", src_dir)
        return 2
    if ingest.TOKEN_SEPARATOR not in str(args.symbol_token):
        # 銘柄だけのトークンは既存 Dukascopy 木の銘柄そのものである。区切りを必須にする
        # ことで、既存木へ書き込む指定がコマンド行の段階で止まる（既存木は書き換えない）。
        LOG.error(
            "--symbol-token に %r が含まれていません: %r。"
            " 銘柄だけのトークンは既存 Dukascopy 木を指します（例: %s）。",
            ingest.TOKEN_SEPARATOR, args.symbol_token, DEFAULT_SYMBOL_TOKEN,
        )
        return 2
    targets = select_months(src_dir, args.months)
    if not targets:
        LOG.error("対象の月 zip がありません: %s（--months %s）", src_dir, args.months)
        return 2

    writer = archive_ingest.DryRunWriter() if args.dry_run else archive_ingest.DayWriter()
    report = archive_ingest.ingest_months(
        targets,
        symbol_token=args.symbol_token,
        data_dir=args.data_dir,
        writer=writer,
    )
    for month in report.month_progress:
        LOG.info(
            "%s: 読み %d 行 / 書込日 %d / 持ち越し %d 行",
            month.month, month.rows_read, month.days_written, month.rows_carried,
        )
    LOG.info(
        "%s月 %s〜%s: 読み %d 行 / 書き %d 行（%d 日）/ 既存日スキップ %d 行（%d 日）"
        " / 空日 %d / 先頭切り落とし %s %d 行 / 持ち越し %d 日 %d 行"
        " / 説明のつかない行 %d%s",
        len(report.months), report.months[0], report.months[-1],
        report.rows_read, report.rows_written, report.days_written,
        report.rows_skipped_existing, report.days_skipped_existing,
        report.days_empty, report.head_dropped_day, report.rows_head_dropped,
        report.days_carried, report.rows_carried,
        report.rows_unaccounted,
        "（dry-run: 書込なし）" if args.dry_run else "",
    )
    if report.rows_unaccounted != 0:
        # 「読んだ行の行方が全部書いてある」は報告の飾りではなく通過条件である。
        # 0 でない値を出したまま正常終了すると、捨てた行が成功の中に埋もれる。
        LOG.error("行方の説明がつかない行が %d 行あります。", report.rows_unaccounted)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    from marketdata.paths import DATA_DIR  # 木と入力の基点は単一権威から採る。

    parser = argparse.ArgumentParser(
        prog="ingest_oanda_archive",
        description="OANDA 月別アーカイブ（月 zip）を tick 木へ取り込む。",
    )
    parser.add_argument("--src-dir", default=str(DATA_DIR / "oanda_ticks" / DEFAULT_SYMBOL),
                        help="月 zip の置き場（既定 DATA_DIR/oanda_ticks/JP225）")
    parser.add_argument("--data-dir", default=str(DATA_DIR),
                        help="tick 木の基点（既定 DATA_DIR）")
    parser.add_argument("--symbol-token", default=DEFAULT_SYMBOL_TOKEN,
                        help=f"書き込み先の銘柄トークン（既定 {DEFAULT_SYMBOL_TOKEN}）")
    parser.add_argument("--months", default=None,
                        help="取り込む年月の範囲 FROM:TO（両端含む・片側省略可）。"
                             "区切り無し（例 2020-05）は単月。既定は全部")
    parser.add_argument("--dry-run", action="store_true",
                        help="1 バイトも書かず、日数と行数だけ報告する")
    parser.add_argument("--verbose", action="store_true", help="DEBUG ログを出す")
    return parser


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return run(args)
    except Mt5SupplyError as exc:
        # Fail-Stop。壊れた月を部分的に取り込んだまま黙って終わらない。
        LOG.error("取り込みを中止しました: %s", exc)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
