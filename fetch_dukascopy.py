#!/usr/bin/env python3
"""Dukascopy から JP225（日経225）1分足を取得し bull_bear_analysis の入力形式へ変換する。

無料・アカウント不要の Dukascopy ヒストリカルデータ（`dukascopy-python`）を用いて
過去数ヶ月〜数年分の 1 分足 OHLCV を取得し、`bull_bear_analysis` が読む CSV 形式
（ヘッダーなし・カンマ区切り・7 列・時刻 ``HH:MM``）へ変換して書き出す。

設計（依存リスク対策）:
    取得部 :func:`fetch_raw`     … ライブラリ呼び出し。将来 ``duka`` 等へ差し替え可能。
    変換部 :func:`to_input_format`… ライブラリ非依存の純粋関数。差し替え時も不変。

タイムゾーン:
    Dukascopy は UTC。日経のデイリー休止は JST 固定 ≈ 21:00 UTC のため、``--tz-offset 3``
    で休止が日境界(0 時)へ寄り、暦日グルーピングが年間通して安定する（既存 MT5 データ
    JP225_M1_*.csv の日次 01:00–23:59 区切りとも整合。検証済み）。

使用例:
    python scripts/fetch_dukascopy.py --start 2025-09-29 --end 2025-09-30 \\
        --output input/JP225_dukascopy_smoke.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import dukascopy_python
from dukascopy_python.instruments import INSTRUMENT_IDX_ASIA_E_N225JAP

# bull_bear_analysis/ ディレクトリ（このファイルの 1 つ上の親）
BASE_DIR = Path(__file__).resolve().parent.parent

# JP225（日経225）= Dukascopy 銘柄 "E_N225Jap"。実機 introspection で確定済み。
DEFAULT_INSTRUMENT = INSTRUMENT_IDX_ASIA_E_N225JAP

# 日経の休止(≈21:00 UTC)を日境界へ寄せる既定オフセット（時間）。検証で確定。
DEFAULT_TZ_OFFSET_HOURS = 3

logger = logging.getLogger("fetch_dukascopy")


def _add_months(d: datetime, months: int) -> datetime:
    """``d`` に ``months`` ヶ月を加算した月初日を返す（チャンク境界生成用）。"""
    month_index = (d.year * 12 + (d.month - 1)) + months
    year, month = divmod(month_index, 12)
    return datetime(year, month + 1, 1)


def _iter_chunks(
    start: datetime, end: datetime, chunk_months: int
) -> List[Tuple[datetime, datetime]]:
    """[start, end] を ``chunk_months`` ヶ月単位の (chunk_start, chunk_end) 区間に分割する。"""
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
    offer_side: str = dukascopy_python.OFFER_SIDE_BID,
):
    """Dukascopy から単一チャンク [chunk_start, chunk_end) の 1 分足を取得する（取得部）。

    戻り値は UTC タイムスタンプ index・列 ``open/high/low/close/volume`` の DataFrame
    （データなしは ``None`` または空 DataFrame）。

    ライブラリ依存はこの関数に限定する。別実装へ差し替える場合もこの関数だけを置換する。
    """
    return dukascopy_python.fetch(
        instrument,
        dukascopy_python.INTERVAL_MIN_1,
        offer_side,
        chunk_start,
        chunk_end,
    )


def stream_to_csv(
    start: datetime,
    end: datetime,
    output_path: Path,
    *,
    tz_offset_hours: int,
    instrument: str = DEFAULT_INSTRUMENT,
    offer_side: str = dukascopy_python.OFFER_SIDE_BID,
    chunk_months: int = 1,
    exclude_weekends: bool = False,
    remove_outliers: bool = True,
    outlier_threshold: float = 0.3,
) -> int:
    """チャンク単位でストリーミング取得→変換→逐次書き出しし、総行数を返す。

    全件をメモリ蓄積する設計は大規模期間で OOM するため（ISSUE-017）、常駐を
    1 チャンク（≈``chunk_months`` ヶ月）に限定する。チャンク境界の重複は直前に
    書き出した UTC タイムスタンプ ``last_ts`` を超える行のみ採用して除去する。
    ``remove_outliers=True``（既定）で日内の外れ値ティックを除去する（ISSUE-019 再発防止）。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    last_ts = None
    total = 0
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for chunk_start, chunk_end in _iter_chunks(start, end, chunk_months):
            logger.info("fetching %s -> %s", chunk_start.date(), chunk_end.date())
            df = fetch_chunk(
                chunk_start, chunk_end, instrument=instrument, offer_side=offer_side
            )
            if df is None or df.empty:
                continue
            # チャンク境界の重複（前チャンク末 == 次チャンク頭）を時系列で除去
            if last_ts is not None:
                df = df[df.index > last_ts]
                if df.empty:
                    continue
            last_ts = df.index.max()
            rows = to_input_format(df, tz_offset_hours, exclude_weekends=exclude_weekends)
            if remove_outliers:
                rows = filter_outlier_ticks(rows, threshold=outlier_threshold)
            writer.writerows(rows)
            total += len(rows)
            del df, rows  # 1 チャンク分のみ常駐させ即時解放
    return total


def to_input_format(df, tz_offset_hours: int, exclude_weekends: bool = False) -> List[List[str]]:
    """UTC OHLCV DataFrame を bull_bear_analysis 入力行へ変換する（純粋・ライブラリ非依存）。

    各行 = ``[日付(YYYY.MM.DD), 時刻(HH:MM), 始値, 高値, 安値, 終値, 出来高]``。
    ``tz_offset_hours`` 分だけ UTC をシフトしてから日付・時刻を切り出す。
    ``exclude_weekends=True`` で、tz補正後のローカル日付が土曜/日曜の行を除外する
    （Dukascopyの週末データは年代で性質が激変＝2012-2013はフラット複製、2015年以降は
    金曜セッション末尾の断片のため、平日のみへ統一する用途）。
    """
    shift = timedelta(hours=tz_offset_hours)
    rows: List[List[str]] = []
    for ts, o, h, low, c, v in zip(
        df.index, df["open"], df["high"], df["low"], df["close"], df["volume"]
    ):
        # tz 情報を持たないナイーブ datetime に正規化してからシフト
        local = ts.to_pydatetime().replace(tzinfo=None) + shift
        if exclude_weekends and local.weekday() >= 5:  # 5=土, 6=日
            continue
        rows.append(
            [
                local.strftime("%Y.%m.%d"),
                local.strftime("%H:%M"),
                f"{float(o)}",
                f"{float(h)}",
                f"{float(low)}",
                f"{float(c)}",
                f"{int(round(float(v)))}",
            ]
        )
    return rows


def filter_outlier_ticks(
    rows: List[List[str]], threshold: float = 0.3
) -> List[List[str]]:
    """日内の外れ値ティックを除去する（純粋・ISSUE-019 再発防止）。

    各ローカル日（``row[0]``）ごとに終値の中央値を求め、OHLC のいずれかが
    中央値から ``threshold``（例 0.3=±30%）を超えて乖離する行を不正ティックとして除外する。
    指数は日中に中央値比 ±30% も動かないため、Dukascopy 配信の区間欠損
    （2025.08.26 で約 -64%）のみを安全に分離できる。

    Args:
        rows: ``to_input_format`` 形式の行（``[日付, 時刻, 始, 高, 安, 終, 出来高]``）。
        threshold: 中央値からの許容相対乖離（0.3 = 30%）。

    Returns:
        不正ティックを除いた行リスト（順序保持）。
    """
    from statistics import median

    by_day: dict[str, List[float]] = {}
    for r in rows:
        by_day.setdefault(r[0], []).append(float(r[5]))
    day_median = {d: median(cs) for d, cs in by_day.items()}

    kept: List[List[str]] = []
    for r in rows:
        med = day_median[r[0]]
        if med <= 0:
            kept.append(r)
            continue
        ohlc = (float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        if any(abs(p / med - 1.0) > threshold for p in ohlc):
            continue  # 不正ティック（中央値から閾値超の乖離）
        kept.append(r)
    return kept


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _resolve_output(value: str) -> Path:
    """出力パスを解決する（相対指定は bull_bear_analysis/ 基準）。"""
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dukascopy から JP225 1分足を取得し bull_bear_analysis 入力形式へ変換する",
    )
    parser.add_argument("--start", required=True, type=_parse_date, help="取得開始日 YYYY-MM-DD（含む）")
    parser.add_argument("--end", required=True, type=_parse_date, help="取得終了日 YYYY-MM-DD（含む）")
    parser.add_argument(
        "--output",
        default="input/JP225_dukascopy.csv",
        help="出力CSVパス（相対は bull_bear_analysis/ 基準。既定: input/JP225_dukascopy.csv）",
    )
    parser.add_argument(
        "--tz-offset",
        type=int,
        default=DEFAULT_TZ_OFFSET_HOURS,
        help="UTC からのシフト時間（既定 +3。日経休止を日境界へ寄せ暦日区切りを安定させる）",
    )
    parser.add_argument("--chunk-months", type=int, default=1, help="分割取得の月数（既定 1）")
    parser.add_argument(
        "--offer-side",
        choices=["bid", "ask"],
        default="bid",
        help="気配側（既定 bid。MT5 OHLC と整合）",
    )
    parser.add_argument(
        "--exclude-weekends",
        action="store_true",
        help="土曜/日曜の行を除外する（週末データの年代差を排し平日のみへ統一）",
    )
    parser.add_argument(
        "--remove-outliers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="日内の外れ値ティックを除去する（既定: 有効 / ISSUE-019 再発防止）。--no-remove-outliers で無効化",
    )
    parser.add_argument(
        "--outlier-threshold",
        type=float,
        default=0.3,
        help="外れ値判定の中央値からの許容相対乖離（既定 0.3=±30%%）",
    )
    parser.add_argument("--quiet", action="store_true", help="進捗ログを抑制する")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")
    # ライブラリ側の冗長ログ（DUKASCRIPT）は警告以上のみに抑制
    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)

    # --end を「その日を含む」よう 1 日加算（fetch は end 未満を返すため）
    fetch_end = args.end + timedelta(days=1)
    offer_side = (
        dukascopy_python.OFFER_SIDE_BID
        if args.offer_side == "bid"
        else dukascopy_python.OFFER_SIDE_ASK
    )

    output_path = _resolve_output(args.output)
    total = stream_to_csv(
        args.start,
        fetch_end,
        output_path,
        tz_offset_hours=args.tz_offset,
        offer_side=offer_side,
        chunk_months=args.chunk_months,
        exclude_weekends=args.exclude_weekends,
        remove_outliers=args.remove_outliers,
        outlier_threshold=args.outlier_threshold,
    )
    if total == 0:
        logger.warning("取得結果が空でした（期間・休場日を確認してください）: %s〜%s", args.start.date(), args.end.date())
        return 1

    logger.info("完了: %d 行を書き出しました -> %s", total, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
