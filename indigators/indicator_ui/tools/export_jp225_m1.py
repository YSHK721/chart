#!/usr/bin/env python3
"""JP225 の 1 分足（原子データ）を dataset ローダが読む CSV へストリーミング書き出しする。

全時間足（5m/15m/1h/4h/1D/1W/1M …）はこの 1 分足を ``dataset.resample_ohlc`` で再集計して
生成する（原子＝1 分足）。``dataset.load_dataframe`` は ``loader.load_ohlc_csv(path,
time_column='date')`` で読むため、``date,open,high,low,close,volume`` 列（ヘッダー付き・
date は UTC ``%Y-%m-%d %H:%M:%S``）で出力する。

OOM 回避（ISSUE-017 と同方針）:
    15 年分の 1 分足（約 500 万行）は全件メモリ展開で OOM するため、``--chunk-months`` 単位で
    ``marketdata.DukascopyCandleSource.fetch_candles`` を反復呼びし、チャンク境界の重複
    （前チャンク末 == 次チャンク頭）を直前書き出し UTC タイムスタンプ超過行のみ採用して除去
    しつつ、逐次 1 行ずつ書き出す。

ベンダ隔離（S3・enabler①解消）:
    取得は marketdata 境界（``DukascopyCandleSource``・volume 付き＝S0）へ委譲し、
    ``dukascopy_python`` への直接依存はこのツールから除去する（ベンダ定数は marketdata の
    ``INTERVALS`` / ``OFFER_SIDES`` 経由で解決）。volume は Candle.volume から取得する。

時刻:
    Dukascopy は UTC。原子はベンダ素のまま UTC で保存し、日足/週足等の境界整合（JST セッション
    境界）は配信側 resample の rule/offset で扱う（原子に tz シフトを焼き込まない）。

使用例:
    # 直近 1 ヶ月（配線実証用・既定出力 DATA_DIR/jp225_m1.csv）
    python tools/export_jp225_m1.py --start 2026-05-01 --end 2026-05-31

    # 全 15 年（バックグラウンド・完了まで数時間）
    python tools/export_jp225_m1.py --start 2011-06-01 --end 2026-06-08
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import os
import sys
import tempfile
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Callable, List, Optional, Tuple

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
# 時系列データの単一基点（marketdata.paths.DATA_DIR・Sd §10.1 C-1）を import するため
# repo 根を sys.path へ。
if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))
import pandas as pd  # noqa: E402（取得部 DataFrame 形の復元・marketdata 委譲後の整形）

from marketdata import (  # noqa: E402（sys.path 設定後に import・ベンダは marketdata で隔離）
    JP225,
    DukascopyCandleSource,
)
from marketdata.paths import DATA_DIR  # noqa: E402

_DEFAULT_OUTPUT = DATA_DIR / "jp225_m1.csv"
# 上位足ロールアップ CSV の既定出力先（rollup_store.path と整合）。
_DEFAULT_ROLLUP_DIR = DATA_DIR / "rollups"
# ロールアップ対象の上位足（1m 原子を除く 5m..1M）。1 分足追記に同期して増分更新する。
_ROLLUP_TIMEFRAMES = ("5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M")

# JP225（日経225）= Dukascopy 銘柄 "E_N225Jap"。marketdata 経由で取り込み（ベンダ隔離）。
DEFAULT_INSTRUMENT = JP225
# 気配側の既定（ベンダ非依存の文字列。fetch_chunk が marketdata.OFFER_SIDES で解決する）。
DEFAULT_OFFER_SIDE = "bid"

# 出力 CSV のヘッダーと date 列書式（stream_to_csv / append_incremental で共用）。
_HEADER = ["date", "open", "high", "low", "close", "volume"]
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LAG_MINUTES = 3
DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 60
# 原子 CSV の起点（last_ts 不在時の取得開始）。原子は素の UTC（tz シフトを焼き込まない）。
DEFAULT_START = datetime(2011, 6, 1)

logger = logging.getLogger("export_jp225_m1")


def _utc_now_naive() -> datetime:
    """現在時刻を UTC ナイーブ datetime で返す（原子は素の UTC・tz を焼き込まない）。

    非推奨の ``utcnow()`` を避け、aware（UTC）→ naive へ明示変換する。
    """
    return datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _df_to_rows(df: Any) -> List[List[Any]]:
    """fetch 結果 DataFrame を ``[date_str, open, high, low, close, volume]`` 行列へ変換する。

    UTC index は素のまま（tz シフトせず）``_DATE_FMT`` で文字列化し、OHLCV は float へ正規化する。
    stream_to_csv / append_incremental で共用（整形ロジックの単一定義）。
    """
    rows: List[List[Any]] = []
    for ts, o, h, low, c, v in zip(
        df.index, df["open"], df["high"], df["low"], df["close"], df["volume"]
    ):
        # UTC のまま素で保存（解像度非依存・配信側 resample で時間足を生成）。
        date_str = ts.to_pydatetime().replace(tzinfo=None).strftime(_DATE_FMT)
        rows.append([date_str, float(o), float(h), float(low), float(c), float(v)])
    return rows


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


def _candles_to_df(candles: List[Any]) -> Any:
    """``CandleSource`` の candles（time=UNIX 秒・volume 付き）を取得部の DataFrame 形へ戻す。

    取得部は従来 tz-aware UTC index・列 ``open/high/low/close/volume`` の DataFrame を返す契約
    （``_df_to_rows`` / 増分の ``df.index`` 操作が依存）。委譲後もこの形を保ち、Candle.time
    （UNIX 秒・UTC）を ``unit="s", utc=True`` で同一 UTC 瞬時の tz-aware index に復元する
    （date 書式・dedup・float repr が委譲前とバイト一致する根拠＝§7.3 oracle）。
    """
    if not candles:
        return pd.DataFrame()
    idx = pd.to_datetime([c["time"] for c in candles], unit="s", utc=True)
    return pd.DataFrame(
        {
            "open": [c["open"] for c in candles],
            "high": [c["high"] for c in candles],
            "low": [c["low"] for c in candles],
            "close": [c["close"] for c in candles],
            "volume": [c["volume"] for c in candles],
        },
        index=idx,
    )


def fetch_chunk(
    chunk_start: datetime,
    chunk_end: datetime,
    *,
    instrument: str = DEFAULT_INSTRUMENT,
    offer_side: str = DEFAULT_OFFER_SIDE,
):
    """単一チャンク [chunk_start, chunk_end) の 1 分足を marketdata 経由で取得する（取得部）。

    ``DukascopyCandleSource``（marketdata adapter・volume 付き＝S0）へ委譲し、ベンダ依存
    （``dukascopy_python``）を marketdata 境界に隔離する（S3・enabler①解消）。戻り値は従来どおり
    tz-aware UTC index・列 ``open/high/low/close/volume`` の DataFrame（無データは空 DataFrame）。
    ``offer_side`` は "bid"/"ask" のベンダ非依存文字列で受け、adapter 構築時にベンダ定数へ写す。
    """
    source = DukascopyCandleSource(
        instrument=instrument,
        interval=_min1_interval(),
        offer_side=_offer_side_const(offer_side),
    )
    return _candles_to_df(source.fetch_candles(chunk_start, chunk_end))


def _min1_interval() -> Any:
    """1 分足の INTERVAL 定数を marketdata 経由で解決する（ベンダ定数を隔離）。"""
    from marketdata import INTERVALS  # 局所 import（marketdata 経由でベンダを隔離）

    return INTERVALS["min_1"]


def _offer_side_const(offer_side: str) -> Any:
    """"bid"/"ask" を OFFER_SIDE 定数へ marketdata 経由で解決する（ベンダ定数を隔離）。"""
    from marketdata import OFFER_SIDES  # 局所 import（marketdata 経由でベンダを隔離）

    return OFFER_SIDES[offer_side]


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
    offer_side: str = DEFAULT_OFFER_SIDE,
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
    # 原子化（🟡-1）: 同一ディレクトリの一時ファイルへストリーム書き→完了時に os.replace で
    #   原子スワップする。再構築中も旧ファイルが有効に保たれ、reader（dataset ローダ）は
    #   torn な中間状態を観測しない。失敗時は一時ファイルを除去し旧ファイルを温存する
    #   （部分書きで上書きしない）。同一 FS 内 rename のため os.replace は原子的。
    fd, tmp_name = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=output_path.name + ".", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(_HEADER)
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
                rows = _df_to_rows(df)
                if repair:
                    rows, dropped = repair_outlier_rows(rows, threshold=repair_threshold)
                    dropped_total += dropped
                writer.writerows(rows)
                total += len(rows)
                del df, rows  # 1 チャンク分のみ常駐させ即時解放
        os.chmod(tmp_path, 0o644)  # mkstemp の 0600 を従来の open("w") 相当へ揃える
        os.replace(tmp_path, output_path)  # 原子スワップ（同一FS rename）
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # 失敗時は旧ファイルを温存（部分書きで上書きしない）
        raise
    if repair and dropped_total:
        logger.info("外れ値補正: %d 本を除去（日内中央値±%.0f%% 超）", dropped_total, repair_threshold * 100)
    return total


def read_last_timestamp(csv_path: Path) -> Optional[datetime]:
    """既存 CSV の最終データ行の ``date`` 列を UTC ナイーブ datetime で返す（純粋寄り・I/O 小）。

    ファイル不在 / 空 / ヘッダーのみ / 末尾不正 → ``None``。末尾改行に頑健。
    """
    path = Path(csv_path)
    if not path.exists():
        return None
    last_data_row: Optional[str] = None
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row:  # 空行（末尾改行など）はスキップ
                continue
            if i == 0 and row[0] == "date":  # ヘッダー行
                continue
            last_data_row = row[0]
    if last_data_row is None:
        return None
    try:
        return datetime.strptime(last_data_row, _DATE_FMT)
    except ValueError:
        return None  # 末尾不正（パース不能）


def compute_fetch_window(
    last_ts: Optional[datetime],
    now: datetime,
    *,
    lag_minutes: int,
    default_start: datetime,
) -> Optional[Tuple[datetime, datetime]]:
    """取得窓 ``(start, end)`` を算出する（純粋・``now`` 注入でテスト固定可能）。

    - ``end = now - lag_minutes``（「数分前まで」境界＝未確定足を除外）。
    - ``start = last_ts`` （無ければ ``default_start``）。
    - ``start >= end`` なら ``None``（取得すべき新規なし）。
    """
    end = now - timedelta(minutes=lag_minutes)
    start = last_ts if last_ts is not None else default_start
    if start >= end:
        return None
    return (start, end)


def append_incremental(
    csv_path: Path,
    *,
    now: datetime,
    lag_minutes: int = DEFAULT_LAG_MINUTES,
    default_start: datetime = DEFAULT_START,
    instrument: str = DEFAULT_INSTRUMENT,
    offer_side: str = DEFAULT_OFFER_SIDE,
    repair: bool = True,
    repair_threshold: float = 0.3,
    rollup_hook: Optional[Callable[[Path, int], None]] = None,
) -> int:
    """既存 CSV の末尾以降を増分取得して追記する（副作用）。追記した行数を返す。

    - 末尾時刻を :func:`read_last_timestamp` で seed し、取得窓を
      :func:`compute_fetch_window` で算出（``None`` なら何もせず 0 を返す）。
    - 追記モード ``"a"``。ファイル新規時のみヘッダー行を書く（既存ファイルへの二重書き禁止）。
    - 既存 ``last_ts`` より後の行のみ採用（重複混入なし）。
    - ``rollup_hook`` 指定時は **追記成功後（return 直前）** に ``rollup_hook(csv_path, added)``
      を呼ぶ（1 分足追記と上位足ロールアップ更新を同期する統合点）。追記 0 のときは呼ばない。
      既定 ``None`` は CLI 既存挙動を不変に保つ（追記のみ・ロールアップを触らない）。
    """
    path = Path(csv_path)
    last_ts = read_last_timestamp(path)
    window = compute_fetch_window(
        last_ts, now, lag_minutes=lag_minutes, default_start=default_start
    )
    if window is None:
        return 0
    start, end = window
    df = fetch_chunk(start, end, instrument=instrument, offer_side=offer_side)
    if df is None or getattr(df, "empty", True):
        return 0
    # fetch の index は tz-aware UTC（dukascopy_python.fetch が utc=True）。原子は UTC 素で
    # 扱い、naive な last_ts と比較するため、ベンダ境界のここで UTC naive へ正規化する。
    if getattr(df.index, "tz", None) is not None:
        df = df.tz_convert("UTC").tz_localize(None)
    if last_ts is not None:
        df = df[df.index > last_ts]  # last_ts 以前の重複を除去
        if df.empty:
            return 0

    rows = _df_to_rows(df)
    if repair:
        rows, _dropped = repair_outlier_rows(rows, threshold=repair_threshold)

    file_exists = path.exists() and path.stat().st_size > 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:  # 新規時のみヘッダー
            writer.writerow(_HEADER)
        writer.writerows(rows)
    added = len(rows)
    # 追記成功後（return 直前）に上位足ロールアップを同期更新する統合点（追記時のみ）。
    if added > 0 and rollup_hook is not None:
        rollup_hook(path, added)
    return added


def build_rollup_hook(
    out_dir: Path = _DEFAULT_ROLLUP_DIR,
    tf_list: Tuple[str, ...] = _ROLLUP_TIMEFRAMES,
) -> Callable[[Path, int], None]:
    """1 分足追記成功後に上位足ロールアップを増分更新する hook を返す（watch 統合点）。

    返す hook ``(csv_path, added)`` は ``rollup_builder.incremental_update`` を当該 1 分足 CSV と
    全上位足（``tf_list``）で呼ぶ。state（``RollupState``）は ``out_dir`` から load し、不在時は
    incremental_update 内で stream_build へフォールバックする（新 state は incremental_update が save）。
    ``rollup_builder`` の import は hook 内に局所化し、CLI の起動時 import 副作用を増やさない。
    """

    def _hook(csv_path: Path, added: int) -> None:
        import rollup_builder as _rb  # 局所 import（CLI 起動コストを増やさない）

        state = _rb.RollupState.load(out_dir)
        _rb.incremental_update(csv_path, state, list(tf_list), out_dir)

    return _hook


def run_watch(
    update_fn: Callable[[], None],
    *,
    interval: int,
    sleep_fn: Callable[[float], None] = _time.sleep,
    stop_after: Optional[int] = None,
) -> int:
    """``update_fn`` → ``sleep_fn(interval)`` を繰り返す薄いポーリングループ（副作用）。

    - ``sleep_fn`` 注入でテスト可能化。``stop_after``（回数）で有限終了。
    - ``update_fn`` の一過性例外（ネットワーク断・一時 fetch 失敗等）は捕捉してログし、
      次インターバルへ継続する（無人ポーリングの可用性を保つ）。
    - ``KeyboardInterrupt`` を捕捉して正常終了（0 を返す）。
    """
    count = 0
    try:
        while True:
            try:
                update_fn()
            except KeyboardInterrupt:
                raise
            except Exception:  # 一過性障害でポーリングを止めない（次インターバルへ継続）
                logger.exception("増分更新に失敗しました（次インターバルへ継続します）")
            count += 1
            if stop_after is not None and count >= stop_after:
                break
            sleep_fn(interval)
    except KeyboardInterrupt:
        return 0
    return 0


def _interval_seconds(value: str) -> int:
    """``--interval`` の型（下限 ``MIN_INTERVAL_SECONDS`` 秒のフロア）。

    60 未満は argparse エラーで拒否する（過剰ポーリング抑止）。
    """
    seconds = int(value)
    if seconds < MIN_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(
            f"--interval は {MIN_INTERVAL_SECONDS} 秒以上を指定してください（指定値: {seconds}）"
        )
    return seconds


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI 引数パーサを構築する（テストから純粋に引数検証できるよう factory 化）。

    既存 ``--start``/``--end``（全期間上書き ``stream_to_csv`` "w" 経路）と既存フラグは
    後方互換のため保持する。``--watch``/``--interval``/``--lag-minutes`` は追加機能。
    """
    parser = argparse.ArgumentParser(
        description="JP225 1分足（原子）を dataset 用 CSV（date,open,high,low,close,volume）へ書き出す",
    )
    # --start/--end は optional。両指定＝全期間上書き（従来）、両省略＝増分（ワンショット/--watch）。
    # 片側のみ指定は曖昧モードのため main で明示エラーにする（required にすると両省略が不可能になる）。
    parser.add_argument("--start", type=_parse_date, help="取得開始日 YYYY-MM-DD（含む。--end と対で全期間上書き）")
    parser.add_argument("--end", type=_parse_date, help="取得終了日 YYYY-MM-DD（含む。--start と対で全期間上書き）")
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
    parser.add_argument(
        "--watch",
        action="store_true",
        help="継続ポーリング（指定で増分を繰り返す。未指定なら起動時ワンショット増分 1 回で終了）",
    )
    parser.add_argument(
        "--interval",
        type=_interval_seconds,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"ポーリング間隔秒（既定 {DEFAULT_INTERVAL_SECONDS} / 下限 {MIN_INTERVAL_SECONDS}）",
    )
    parser.add_argument(
        "--lag-minutes",
        type=int,
        default=DEFAULT_LAG_MINUTES,
        help=f"「数分前まで」境界＝未確定足を除外する分数（既定 {DEFAULT_LAG_MINUTES}）",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s"
    )
    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)

    # 片側だけ指定（--start のみ / --end のみ）は曖昧モードのためエラー（曖昧モードを作らない）。
    if (args.start is None) != (args.end is None):
        parser.error("--start と --end は両方指定するか、両方省略してください（増分モード）")

    range_given = args.start is not None and args.end is not None
    # 気配側はベンダ非依存の文字列（"bid"/"ask"）のまま下流へ渡す（ベンダ定数化は
    # fetch_chunk が marketdata.OFFER_SIDES で行う＝vendor 隔離）。
    offer_side = args.offer_side

    # 増分モード（--start/--end 省略）の default_start は原子起点を使う。
    incr_default_start = args.start if args.start is not None else DEFAULT_START

    # 1 分足追記成功後に上位足ロールアップを同期更新する hook（増分モードで配線）。
    rollup_hook = build_rollup_hook()

    if not range_given and not args.watch:
        # 起動時ワンショット増分：既存末尾以降を 1 回だけ追記して終了。
        now_utc_naive = _utc_now_naive()
        added = append_incremental(
            args.output,
            now=now_utc_naive,
            lag_minutes=args.lag_minutes,
            default_start=incr_default_start,
            offer_side=offer_side,
            repair=args.repair,
            repair_threshold=args.repair_threshold,
            rollup_hook=rollup_hook,
        )
        if added:
            logger.info("増分追記: %d 行 -> %s", added, args.output)
        else:
            logger.info("増分なし（取得すべき新規データなし）")
        return 0

    if args.watch:
        # 継続ポーリング：既存末尾以降を増分追記し、interval 秒ごとに繰り返す。
        def _update() -> None:
            # 原子は素の UTC（naive）で扱う（_utc_now_naive: aware→naive 変換）。
            now_utc_naive = _utc_now_naive()
            added = append_incremental(
                args.output,
                now=now_utc_naive,
                lag_minutes=args.lag_minutes,
                default_start=incr_default_start,
                offer_side=offer_side,
                repair=args.repair,
                repair_threshold=args.repair_threshold,
                rollup_hook=rollup_hook,
            )
            if added:
                logger.info("増分追記: %d 行 -> %s", added, args.output)

        logger.info("watch 開始（interval=%ds, lag=%dmin）", args.interval, args.lag_minutes)
        return run_watch(_update, interval=args.interval)

    # 全期間上書き（従来据置）：--end を「その日を含む」よう 1 日加算（fetch は end 未満を返す）。
    fetch_end = args.end + timedelta(days=1)
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
