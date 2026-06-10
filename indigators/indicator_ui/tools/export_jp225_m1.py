#!/usr/bin/env python3
"""JP225 の 1 分足（原子データ）を dataset ローダが読む CSV へストリーミング書き出しする。

全時間足（5m/15m/1h/4h/1D/1W/1M …）はこの 1 分足を ``dataset.resample_ohlc`` で再集計して
生成する（原子＝1 分足）。``dataset.load_dataframe`` は ``loader.load_ohlc_csv(path,
time_column='date')`` で読むため、``date,open,high,low,close,volume`` 列（ヘッダー付き・
date は UTC ``%Y-%m-%d %H:%M:%S``）で出力する。

OOM 回避（ISSUE-017 と同方針）:
    15 年分の 1 分足（約 500 万行）は全件メモリ展開で OOM するため、``--chunk-months`` 単位で
    ``dukascopy_python.fetch`` を呼び、チャンク境界の重複（前チャンク末 == 次チャンク頭）を
    直前書き出し UTC タイムスタンプ超過行のみ採用して除去しつつ、逐次 1 行ずつ書き出す。
    ライブラリ依存（dukascopy_python）はこのツールに限定する（marketdata の Candle は volume を
    持たないため、原子に volume を残す目的でライブラリを直接呼ぶ）。

時刻:
    Dukascopy は UTC。原子はベンダ素のまま UTC で保存し、日足/週足等の境界整合（JST セッション
    境界）は配信側 resample の rule/offset で扱う（原子に tz シフトを焼き込まない）。

使用例:
    # 直近 1 ヶ月（配線実証用・既定出力 marketdata/data/jp225_m1.csv）
    python tools/export_jp225_m1.py --start 2026-05-01 --end 2026-05-31

    # 全 15 年（バックグラウンド・完了まで数時間）
    python tools/export_jp225_m1.py --start 2011-06-01 --end 2026-06-08
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, List, Tuple

import dukascopy_python
from dukascopy_python.instruments import INSTRUMENT_IDX_ASIA_E_N225JAP

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_OUTPUT = _WORKSPACE_ROOT / "marketdata" / "data" / "jp225_m1.csv"

# JP225（日経225）= Dukascopy 銘柄 "E_N225Jap"。
DEFAULT_INSTRUMENT = INSTRUMENT_IDX_ASIA_E_N225JAP

logger = logging.getLogger("export_jp225_m1")


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _add_months(d: datetime, months: int) -> datetime:
    """``d`` に ``months`` ヶ月を加算した月初日を返す（チャンク境界生成用）。"""
    month_index = (d.year * 12 + (d.month - 1)) + months
    year, month = divmod(month_index, 12)
    return datetime(year, month + 1, 1)


def _iter_chunks(
    start: datetime, end: datetime, chunk_months: int
) -> List[Tuple[datetime, datetime]]:
    """[start, end) を ``chunk_months`` ヶ月単位の (chunk_start, chunk_end) 区間に分割する。"""
    chunks: List[Tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        nxt = min(_add_months(cursor, chunk_months), end)
        chunks.append((cursor, nxt))
        cursor = nxt
    return chunks


def fetch_chunk(
    chunk_start: datetime,
    chunk_end: datetime,
    *,
    instrument: str = DEFAULT_INSTRUMENT,
    offer_side: Any = dukascopy_python.OFFER_SIDE_BID,
):
    """Dukascopy から単一チャンク [chunk_start, chunk_end) の 1 分足を取得する（取得部）。

    戻り値は UTC index・列 ``open/high/low/close/volume`` の DataFrame（無データは None/空）。
    ライブラリ依存はこの関数に限定する（差し替え時もここだけを置換）。
    """
    return dukascopy_python.fetch(
        instrument,
        dukascopy_python.INTERVAL_MIN_1,
        offer_side,
        chunk_start,
        chunk_end,
    )


def repair_outlier_rows(
    rows: List[List[Any]], threshold: float = 0.3
) -> Tuple[List[List[Any]], int]:
    """日内の外れ値バーを除去する（純粋・``filter_outlier_ticks`` と同基準）。

    各暦日（``row[0][:10]``）ごとに close の中央値を求め、OHLC のいずれかが中央値から
    ``threshold``（0.3=±30%）を超えて乖離するバーを不正（配信欠損・ファントム）として除外する。
    指数は日中に中央値比 ±30% も動かないため、Dukascopy の区間欠損（例 2025-08-26 の約 -64%）
    のみを安全に分離できる。月チャンク境界＝日境界のため、チャンク内で各日のバーは完結する
    （日をまたいで中央値が分断されない）。

    Args:
        rows: ``[date_str, open, high, low, close, volume]`` の行（数値は float）。
        threshold: 中央値からの許容相対乖離（0.3=30%）。

    Returns:
        (除去後の行リスト（順序保持）, 除去件数)。
    """
    by_day: dict[str, List[float]] = {}
    for r in rows:
        by_day.setdefault(r[0][:10], []).append(float(r[4]))
    day_median = {d: median(cs) for d, cs in by_day.items()}

    kept: List[List[Any]] = []
    dropped = 0
    for r in rows:
        med = day_median[r[0][:10]]
        if med <= 0:
            kept.append(r)
            continue
        ohlc = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        if any(abs(p / med - 1.0) > threshold for p in ohlc):
            dropped += 1
            continue  # 不正バー（中央値から閾値超の乖離）
        kept.append(r)
    return kept, dropped


def stream_to_csv(
    start: datetime,
    end: datetime,
    output_path: Path,
    *,
    instrument: str = DEFAULT_INSTRUMENT,
    offer_side: Any = dukascopy_python.OFFER_SIDE_BID,
    chunk_months: int = 1,
    repair: bool = True,
    repair_threshold: float = 0.3,
) -> int:
    """チャンク単位でストリーミング取得→（外れ値補正）→逐次書き出しし、総行数を返す（OOM 回避）。

    常駐は 1 チャンク（≈``chunk_months`` ヶ月）に限定する。チャンク境界の重複は直前に書き出した
    UTC タイムスタンプ ``last_ts`` を超える行のみ採用して除去する。``repair=True``（既定）で
    チャンク内の日内外れ値バーを :func:`repair_outlier_rows` で除去してから書き出す
    （再取得時の配信欠損ファントム再混入を防ぐ）。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    last_ts = None
    total = 0
    dropped_total = 0
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for chunk_start, chunk_end in _iter_chunks(start, end, chunk_months):
            logger.info("fetching %s -> %s", chunk_start.date(), chunk_end.date())
            df = fetch_chunk(
                chunk_start, chunk_end, instrument=instrument, offer_side=offer_side
            )
            if df is None or df.empty:
                continue
            if last_ts is not None:
                df = df[df.index > last_ts]
                if df.empty:
                    continue
            last_ts = df.index.max()
            # チャンク（≈1 ヶ月）分の行を組み、日内外れ値を補正してから書き出す。
            #   月チャンク境界＝日境界のため、各日のバーはこのチャンク内で完結する。
            rows: List[List[Any]] = []
            for ts, o, h, low, c, v in zip(
                df.index, df["open"], df["high"], df["low"], df["close"], df["volume"]
            ):
                # UTC のまま素で保存（解像度非依存・配信側 resample で時間足を生成）。
                date_str = (
                    ts.to_pydatetime().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
                )
                rows.append([date_str, float(o), float(h), float(low), float(c), float(v)])
            if repair:
                rows, dropped = repair_outlier_rows(rows, threshold=repair_threshold)
                dropped_total += dropped
            writer.writerows(rows)
            total += len(rows)
            del df, rows  # 1 チャンク分のみ常駐させ即時解放
    if repair and dropped_total:
        logger.info("外れ値補正: %d 本を除去（日内中央値±%.0f%% 超）", dropped_total, repair_threshold * 100)
    return total


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="JP225 1分足（原子）を dataset 用 CSV（date,open,high,low,close,volume）へ書き出す",
    )
    parser.add_argument("--start", required=True, type=_parse_date, help="取得開始日 YYYY-MM-DD（含む）")
    parser.add_argument("--end", required=True, type=_parse_date, help="取得終了日 YYYY-MM-DD（含む）")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--offer-side", choices=["bid", "ask"], default="bid")
    parser.add_argument("--chunk-months", type=int, default=1, help="分割取得の月数（既定 1）")
    parser.add_argument(
        "--repair",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="日内の外れ値バーを除去する（既定: 有効 / 配信欠損ファントム再混入防止）。--no-repair で無効化",
    )
    parser.add_argument(
        "--repair-threshold",
        type=float,
        default=0.3,
        help="外れ値判定の日内中央値からの許容相対乖離（既定 0.3=±30%%）",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s"
    )
    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)

    # --end を「その日を含む」よう 1 日加算（fetch は end 未満を返すため）。
    fetch_end = args.end + timedelta(days=1)
    offer_side = (
        dukascopy_python.OFFER_SIDE_BID
        if args.offer_side == "bid"
        else dukascopy_python.OFFER_SIDE_ASK
    )

    logger.info("fetching JP225 min_1  %s 〜 %s", args.start.date(), args.end.date())
    total = stream_to_csv(
        args.start,
        fetch_end,
        args.output,
        offer_side=offer_side,
        chunk_months=args.chunk_months,
        repair=args.repair,
        repair_threshold=args.repair_threshold,
    )
    if total == 0:
        logger.warning("取得結果が空でした（期間・休場日を確認してください）")
        return 1

    logger.info("書き出し完了: %d 行 -> %s", total, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
