#!/usr/bin/env python3
"""marketdata の JP225 を、indicator_ui の dataset ローダが読む CSV へ書き出す。

B方式（サーバ）の ``datasetRef='jp225'`` 用データを生成する。``dataset.load_dataframe`` は
``loader.load_ohlc_csv(path, time_column='date')`` で読むため、``date,open,high,low,close``
列（ヘッダー付き）で出力する。外れ値（2025-08-26 等）は補正してから書き出す。

出力既定: ``DATA_DIR/jp225_daily.csv``（dataset.py の whitelist と対応）。
既存データ（sample 系）には一切触れない。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import dukascopy_python

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))

from marketdata import (  # noqa: E402
    INTERVALS,
    DukascopyCandleSource,
    repair_ohlc_outliers,
)
from marketdata.paths import DATA_DIR  # noqa: E402

# 時系列データの単一基点（marketdata.paths.DATA_DIR・Sd §10.1 C-1）配下へ集約。
_DEFAULT_OUTPUT = DATA_DIR / "jp225_daily.csv"

logger = logging.getLogger("export_jp225_csv")


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="marketdata の JP225 を dataset 用 CSV（date,open,high,low,close）へ書き出す",
    )
    parser.add_argument("--start", type=_parse_date, default=_parse_date("2022-01-01"))
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--interval", choices=list(INTERVALS), default="day_1")
    parser.add_argument("--offer-side", choices=["bid", "ask"], default="bid")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--repair", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--repair-threshold", type=float, default=0.3)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(message)s")
    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)

    end = args.end or datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    fetch_end = end + timedelta(days=1)
    offer_side = (dukascopy_python.OFFER_SIDE_BID if args.offer_side == "bid"
                  else dukascopy_python.OFFER_SIDE_ASK)
    # 日足以下は date 列を日付のみにすると同日重複するため、足種で日時書式を切り替える。
    date_fmt = "%Y-%m-%d" if args.interval == "day_1" else "%Y-%m-%d %H:%M:%S"

    source = DukascopyCandleSource(interval=INTERVALS[args.interval], offer_side=offer_side)
    logger.info("fetching JP225 %s  %s 〜 %s", args.interval, args.start.date(), end.date())
    candles = source.fetch_candles(args.start, fetch_end)
    if not candles:
        logger.warning("取得結果が空でした（期間・休場日を確認してください）")
        return 1
    if args.repair:
        candles, fixes = repair_ohlc_outliers(candles, threshold=args.repair_threshold)
        for line in fixes:
            logger.info("外れ値補正:%s", line)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close"])
        for c in candles:
            d = dt.datetime.fromtimestamp(c["time"], dt.timezone.utc).strftime(date_fmt)
            writer.writerow([d, c["open"], c["high"], c["low"], c["close"]])

    logger.info("書き出し完了: %d 行 -> %s", len(candles), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
