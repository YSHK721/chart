"""Dukascopy から実ティック（INTERVAL_TICK）を取得し raw landing として保存する。

2 段パイプラインの段1（raw landing＝取得物を不変アーカイブ）。Dukascopy ネイティブ列
(bidPrice/askPrice/bidVolume/askVolume + timestamp[UTC,ms]) をそのまま Parquet へ保存する。
canonical スキーマ(timestamp/bid/ask/last/volume)への変換＝段2は tick-store ingest 側の責務。

使い方:
    python backtest/tools/fetch_ticks_dukascopy.py \
        --start 2025-01-02 --end 2025-01-31 \
        --output marketdata/ticks/JP225_ticks_202501_raw.parquet
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import dukascopy_python as d
from dukascopy_python.instruments import INSTRUMENT_IDX_ASIA_E_N225JAP as N225


def fetch_range(start: dt.datetime, end: dt.datetime, offer_side: str) -> pd.DataFrame:
    """[start, end) を日次チャンクで取得し連結（resilient・進捗ログ）。"""
    frames: list[pd.DataFrame] = []
    day = start
    while day < end:
        nxt = min(day + dt.timedelta(days=1), end)
        try:
            df = d.fetch(N225, d.INTERVAL_TICK, offer_side, day, nxt)
        except Exception as exc:  # noqa: BLE001 (取得失敗日はスキップ・継続)
            print(f"  WARN {day:%Y-%m-%d}: fetch失敗 skip ({exc})", flush=True)
            day = nxt
            continue
        n = len(df)
        if n:
            frames.append(df)
        print(f"  {day:%Y-%m-%d}: {n} ticks (累計 {sum(len(f) for f in frames)})", flush=True)
        day = nxt
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames).sort_index()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Dukascopy 実ティック取得（raw landing）")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True, help="この日を含む（[start, end+1d) で取得）")
    p.add_argument("--output", required=True)
    p.add_argument("--offer-side", choices=["bid", "ask"], default="bid")
    a = p.parse_args(argv)

    start = dt.datetime.fromisoformat(a.start)
    end = dt.datetime.fromisoformat(a.end) + dt.timedelta(days=1)  # end を含む
    side = d.OFFER_SIDE_BID if a.offer_side == "bid" else d.OFFER_SIDE_ASK

    print(f"fetch JP225 ticks {a.start}..{a.end} (offer={a.offer_side})", flush=True)
    df = fetch_range(start, end, side)
    if df.empty:
        print("ERROR: 取得行ゼロ", flush=True)
        return 1
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # timestamp を列に出して保存（tz-aware UTC を保持）
    df = df.reset_index().rename(columns={"index": "timestamp"})
    df.to_parquet(out, index=False)
    print(f"DONE: {len(df)} ticks -> {out} ({out.stat().st_size/1e6:.1f} MB)", flush=True)
    print(f"  範囲 {df['timestamp'].min()} .. {df['timestamp'].max()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
