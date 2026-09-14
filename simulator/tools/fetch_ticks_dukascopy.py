"""Dukascopy から実ティック（INTERVAL_TICK）を取得し raw landing として保存する。

2 段パイプラインの段1（raw landing＝取得物を不変アーカイブ）。Dukascopy ネイティブ列
(bidPrice/askPrice/bidVolume/askVolume + timestamp[UTC,ms]) をそのまま Parquet へ保存する。
canonical スキーマ(timestamp/bid/ask/last/volume)への変換＝段2は tick-store ingest 側の責務。

取得ロジックは marketdata の :class:`DukascopyTickSource`（保存前 raw ティック取得の具象・
旧 TickSource・ISSUE-092 ⑧で Protocol 撤去・enabler②）へ移管済み。本スクリプトは後方互換の
CLI（raw parquet 保存）を提供する薄い委譲ラッパである。

使い方:
    python simulator/tools/fetch_ticks_dukascopy.py \
        --start 2025-01-02 --end 2025-01-31 \
        --output marketdata/ticks/JP225_ticks_202501_raw.parquet
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd


def fetch_range(start: dt.datetime, end: dt.datetime, offer_side: object = None) -> pd.DataFrame:
    """[start, end) を取得し timestamp 列の raw frame を返す（DukascopyTickSource 委譲）。

    取得ロジックは ``marketdata.DukascopyTickSource.fetch_ticks`` へ移管済み。戻り DataFrame は
    timestamp を**列**に持つ（H-2・reset_index 済）。``offer_side`` 引数は後方互換のため残すが
    無視される（H-3: bidPrice/askPrice 両列を常に返すため気配側の単一指定は不要）。
    """
    from marketdata import DukascopyTickSource

    return DukascopyTickSource().fetch_ticks(start, end)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Dukascopy 実ティック取得（raw landing）")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True, help="この日を含む（[start, end+1d) で取得）")
    p.add_argument("--output", required=True)
    p.add_argument("--offer-side", choices=["bid", "ask"], default="bid")
    a = p.parse_args(argv)

    start = dt.datetime.fromisoformat(a.start)
    end = dt.datetime.fromisoformat(a.end) + dt.timedelta(days=1)  # end を含む

    # H-3: raw tick は bidPrice/askPrice 両列を常に返す（DukascopyTickSource）。
    # --offer-side は後方互換のため受理するがログ表示のみ（取得側で気配側は単一指定しない）。
    print(f"fetch JP225 ticks {a.start}..{a.end} (offer={a.offer_side})", flush=True)
    df = fetch_range(start, end)
    if df.empty:
        print("ERROR: 取得行ゼロ", flush=True)
        return 1
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # DukascopyTickSource が既に timestamp を列へ出している（H-2・reset_index 済）。
    df.to_parquet(out, index=False)
    print(f"DONE: {len(df)} ticks -> {out} ({out.stat().st_size/1e6:.1f} MB)", flush=True)
    print(f"  範囲 {df['timestamp'].min()} .. {df['timestamp'].max()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
