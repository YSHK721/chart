"""上位足の増分ロールアップ（メモリ有界化）。

server が 4.5M 行 / 284MB の 1 分足を全ロードして OOM する問題に対し、1 分足を二度と
全ロードしない設計の生成側を担う。1 分足を ``pd.read_csv(chunksize=...)`` でストリーム読みし、
各時間足（TF）を :func:`dataset.resample_ohlc`（dataset の規則を再利用・再実装しない）で集約して
TF 別ロールアップ CSV（``date,open,high,low,close,volume``・loader 互換）へ書き出す。

メモリ有界（厳守）:
    全行を同時に pandas へ載せない。``chunk_rows`` 単位でストリーム読みし、チャンク跨ぎの未確定
    period は carry-over（確定まで書き出さない・D-1）する。:func:`merge_same_period` の結合性が
    チャンク分割と全件 resample の数値一致を保証する。

数値一致の根拠:
    resample 規則（W-FRI/ME/5min/tz/closed/label）を再実装せず必ず :func:`dataset.resample_ohlc`
    を呼ぶ。チャンク末尾の未確定 period のみ次チャンクへ繰り越し、確定 period のみ書き出すことで
    ``stream_build`` 結果は ``resample_ohlc(全件, rule)`` と完全一致する。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

# dataset（api/adapter/compute/dataset.py）の resample 規則を再利用する（再実装しない）。
# rollup_builder.py: tools/ → parents[1] = indicator_ui。api/ を import パスへ追加する。
_INDICATOR_UI_DIR = Path(__file__).resolve().parents[1]
_API_DIR = _INDICATOR_UI_DIR / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from adapter.compute import dataset, tail_reader  # noqa: E402

# 増分更新で「state 以降の新規 1 分足」を逆シークで拾う末尾 probe 行数。--watch は毎分 ~1 行
# 追記なので十分大（≈14 日分の連続 1 分足）。probe が last_ts を内包できない長期 catch-up のみ
# 全件スキャンへフォールバックする。
_INCREMENTAL_TAIL_PROBE_ROWS = 20_000

# ロールアップ CSV の列（loader 互換: date + OHLCV）。
_HEADER = ["date", "open", "high", "low", "close", "volume"]
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
# ロールアップ CSV のファイル名 prefix（jp225_m1 由来・<prefix>_<tf>.csv）。
_REF_PREFIX = "jp225_m1"
_STATE_FILENAME = "rollup_state.json"


def merge_same_period(prev_bar: dict[str, Any], new_bar: dict[str, Any]) -> dict[str, Any]:
    """同一 period の 2 つの partial bar を結合する（結合的）。

    open=最初の bar の open / high=max / low=min / close=後の bar の close / volume=合算。
    結合的（``merge(merge(a,b),c) == merge(a,merge(b,c))``）であり、チャンク跨ぎ carry-over の
    正しさ（D-1）の根拠となる。
    """
    return {
        "open": prev_bar["open"],
        "high": max(prev_bar["high"], new_bar["high"]),
        "low": min(prev_bar["low"], new_bar["low"]),
        "close": new_bar["close"],
        "volume": prev_bar["volume"] + new_bar["volume"],
    }


@dataclass
class RollupState:
    """ロールアップ進捗状態（増分更新の基点）。``last_processed_ts`` 以降だけを増分処理する。"""

    last_processed_ts: datetime

    def save(self, out_dir: Path) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {"last_processed_ts": self.last_processed_ts.strftime(_DATE_FMT)}
        (out_dir / _STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, out_dir: Path) -> Optional["RollupState"]:
        path = Path(out_dir) / _STATE_FILENAME
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        ts = datetime.strptime(payload["last_processed_ts"], _DATE_FMT)
        return cls(last_processed_ts=ts)


def _rollup_path(out_dir: Path, tf: str) -> Path:
    return Path(out_dir) / f"{_REF_PREFIX}_{tf}.csv"


def _bar_to_dict(row: pd.Series) -> dict[str, Any]:
    return {
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
    }


def _bar_to_csv_row(period: Any, bar: dict[str, Any]) -> list[Any]:
    """(period, bar) を ロールアップ CSV の 1 行（loader 互換）へ整形する（単一定義）。

    ``_write_rollup``（全件一括書き）と :class:`_RollupWriter`（逐次 flush）で同一フォーマットを
    共用し、両経路の出力 CSV をバイト一致させるための行整形の単一真実源。
    """
    return [
        pd.Timestamp(period).strftime(_DATE_FMT),
        bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"],
    ]


def _write_rollup(out_dir: Path, tf: str, bars: dict[Any, dict[str, Any]]) -> None:
    """period→bar の辞書を date 昇順でロールアップ CSV へ**原子的に**書き出す（loader 互換形式）。

    原子化（🔴）: 同一ディレクトリの一時ファイルへ書き切ってから ``os.replace`` で確定パスへ
    swap する。``--watch`` が毎分この全書き直しを行うため、書込中の crash/OOM-kill で確定パスに
    部分 CSV が残ると、cold-start の server が torn-read フォールバック不能のまま不完全データを
    配信する。tmp→replace で確定パスは「完全な新 CSV」か「旧 CSV」のいずれかに限定する
    （既存 ``export_jp225_m1.stream_to_csv`` と同方式）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    import csv as _csv

    final = _rollup_path(out_dir, tf)
    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), prefix=final.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(_HEADER)
            for period in sorted(bars):
                w.writerow(_bar_to_csv_row(period, bars[period]))
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_rollup_df(out_dir: Path, tf: str, df: pd.DataFrame) -> None:
    """date-index OHLCV DataFrame を date 昇順でロールアップ CSV へ**原子的に**書く（増分経路）。

    増分更新（ISSUE-012）の memory-bounded 経路。``_write_rollup``（辞書版）と同じ tmp→``os.replace``
    の原子化で確定パスを「完全な新 CSV」か「旧 CSV」のいずれかに限定する。出力列・date 書式
    （``_DATE_FMT``）・ヘッダは loader 互換（``_HEADER``）で揃える。90 万件規模でも辞書化せず
    pandas の to_csv をストリーム書きするため RSS は DataFrame 1〜2 個分に有界化する。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = _rollup_path(out_dir, tf)
    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), prefix=final.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        out = df[["open", "high", "low", "close", "volume"]].sort_index()
        out = out.copy()
        out.index = pd.DatetimeIndex(out.index).strftime(_DATE_FMT)
        out.index.name = "date"
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            out.to_csv(fh, header=_HEADER[1:], index_label=_HEADER[0])
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class _RollupWriter:
    """確定 period バーを date 昇順で逐次ファイルへ flush する writer（メモリ有界化）。

    ``stream_build`` の確定バー streaming-write 化（巨大期間でも確定済みバーを蓄積せず即時 flush）。
    ヘッダを最初に 1 行書き、以後 :meth:`write` を確定順（＝昇順）に呼ぶ。出力 CSV の内容・行順序は
    ``_write_rollup`` と完全一致する（行整形は :func:`_bar_to_csv_row` で共用・バイト一致）。

    原子化（🔴）: 一時ファイルへ逐次 flush し、:meth:`commit`（成功時）で ``os.replace`` で確定
    パスへ swap する。:meth:`close` は未 commit なら tmp を破棄し確定パスを汚さない。これにより
    書込中 crash でも確定パスは「完全な新 CSV」か「旧 CSV」のいずれかに限定される。
    """

    def __init__(self, out_dir: Path, tf: str) -> None:
        import csv as _csv

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._final = _rollup_path(out_dir, tf)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(out_dir), prefix=self._final.name + ".", suffix=".tmp"
        )
        self._tmp: Optional[Path] = Path(tmp_name)
        self._fh = os.fdopen(fd, "w", newline="", encoding="utf-8")
        self._w = _csv.writer(self._fh)
        self._w.writerow(_HEADER)
        self._committed = False

    def write(self, period: Any, bar: dict[str, Any]) -> None:
        """確定済み 1 バーを 1 行 flush する（呼び出しは date 昇順であること）。"""
        self._w.writerow(_bar_to_csv_row(period, bar))

    def commit(self) -> None:
        """tmp を閉じ確定パスへ原子スワップする（成功時のみ呼ぶ）。"""
        if self._committed:
            return
        self._fh.close()
        os.replace(self._tmp, self._final)
        self._committed = True
        self._tmp = None

    def close(self) -> None:
        """fh を閉じる。未 commit なら tmp を破棄して確定パスを汚さない（冪等）。"""
        if not self._fh.closed:
            self._fh.close()
        if not self._committed and self._tmp is not None:
            self._tmp.unlink(missing_ok=True)
            self._tmp = None

    def __enter__(self) -> "_RollupWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _resample_chunk(chunk: pd.DataFrame, rule: str | None) -> "OrderedBars":
    """チャンク（date index 化済み）を rule で resample し period→bar の順序辞書を返す。"""
    resampled = dataset.resample_ohlc(chunk, rule)
    bars: "OrderedBars" = {}
    for period, row in resampled.iterrows():
        bars[period] = _bar_to_dict(row)
    return bars


# 型エイリアス（period(Timestamp) → bar(dict)）。挿入順＝date 昇順を保つ。
OrderedBars = dict


def _index_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    """read_csv のチャンク（date 列を持つ）を date を DatetimeIndex にして返す。"""
    chunk = chunk.copy()
    chunk["date"] = pd.to_datetime(chunk["date"])
    return chunk.set_index("date")


def stream_build(
    m1_csv_path: Path,
    tf_list: Iterable[str],
    out_dir: Path,
    chunk_rows: int = 500_000,
) -> "RollupState":
    """1 分足を chunk 単位でストリーム読みし、各 TF をロールアップ CSV へ書き出す（メモリ有界）。

    チャンク跨ぎの未確定（最終）period は carry-over し、確定するまで書き出さない（D-1）。
    全行を同時に DataFrame 化しない（``pd.read_csv(chunksize=chunk_rows)``）。
    """
    tf_list = list(tf_list)
    m1_csv_path = Path(m1_csv_path)
    # TF ごとに確定済み period バーを逐次 flush する writer（確定バーを蓄積しない＝メモリ有界化）。
    #   常駐は 1 chunk ＋ TF ごとの carry-over 境界バー 1 本のみ（巨大期間でも確定バーが嵩まない）。
    writer_by_tf: dict[str, _RollupWriter] = {tf: _RollupWriter(out_dir, tf) for tf in tf_list}
    # TF ごとに「未確定（carry-over 中）の最終 period→bar」を 1 本だけ保持する。
    pending_by_tf: dict[str, Optional[tuple[Any, dict[str, Any]]]] = {tf: None for tf in tf_list}

    last_ts: Optional[pd.Timestamp] = None

    try:
        for raw_chunk in pd.read_csv(m1_csv_path, chunksize=chunk_rows):
            chunk = _index_chunk(raw_chunk)
            if not chunk.empty:
                last_ts = chunk.index.max()
            for tf in tf_list:
                rule = dataset.TIMEFRAME_RULES[tf]
                chunk_bars = _resample_chunk(chunk, rule)
                if not chunk_bars:
                    continue
                periods = list(chunk_bars)
                # 直前チャンクの未確定 period が、このチャンク先頭 period と一致するならマージ。
                pending = pending_by_tf[tf]
                if pending is not None:
                    p_period, p_bar = pending
                    if periods and periods[0] == p_period:
                        chunk_bars[p_period] = merge_same_period(p_bar, chunk_bars[p_period])
                    else:
                        # 一致しなければ直前未確定 period は確定済み（後続で延びない）→ 即 flush。
                        #   p_period（前チャンク最終）< periods[0] のため確定順＝昇順を保つ。
                        writer_by_tf[tf].write(p_period, p_bar)
                # このチャンクの最終 period は次チャンクへ延びうるため未確定として carry-over。
                last_period = periods[-1]
                new_pending_bar = chunk_bars.pop(last_period)
                # 残り（最終 period 以外）は確定として昇順 flush する（蓄積しない）。
                for period, bar in chunk_bars.items():
                    writer_by_tf[tf].write(period, bar)
                pending_by_tf[tf] = (last_period, new_pending_bar)

        # 全チャンク終了後、残った未確定 period を確定して flush する（各 TF の最終バー）。
        for tf in tf_list:
            pending = pending_by_tf[tf]
            if pending is not None:
                writer_by_tf[tf].write(pending[0], pending[1])
        # 成功時のみ各 TF の tmp を確定パスへ原子スワップ（🔴）。例外時は finally の close が
        #   tmp を破棄し確定パスを汚さない。
        for w in writer_by_tf.values():
            w.commit()
    finally:
        for w in writer_by_tf.values():
            w.close()

    state = RollupState(
        last_processed_ts=(last_ts.to_pydatetime() if last_ts is not None else datetime.min)
    )
    state.save(out_dir)
    return state


def incremental_update(
    m1_csv_path: Path,
    state: Optional[RollupState],
    tf_list: Iterable[str],
    out_dir: Path,
) -> "RollupState":
    """state 以降の追記 tail のみ読み、各 TF ロールアップ末尾へマージする（増分・メモリ有界）。

    state 不在（None）は初回として :func:`stream_build` へフォールバックする。同一 period は
    上書き（形成中バー更新）、新規 period は append（period クローズ＝確定 append）する。
    """
    tf_list = list(tf_list)
    if state is None:
        return stream_build(m1_csv_path, tf_list, out_dir)

    last_ts = pd.Timestamp(state.last_processed_ts)
    # まず末尾 probe（逆シーク）で state 以降の新規行を拾う。--watch は毎分 ~1 行追記なので
    # probe（≈14 日分）が last_ts を内包し、全件スキャンを避けられる（OOM 回避の本丸）。
    probe = tail_reader.read_tail(Path(m1_csv_path), _INCREMENTAL_TAIL_PROBE_ROWS)
    if not probe.empty and probe.index.min() <= last_ts:
        tail_df = probe[probe.index > last_ts]
    else:
        # probe が last_ts を内包できない長期 catch-up のみ全件ストリームへフォールバック。
        tail_frames: list[pd.DataFrame] = []
        for raw_chunk in pd.read_csv(m1_csv_path, chunksize=500_000):
            chunk = _index_chunk(raw_chunk)
            t = chunk[chunk.index > last_ts]
            if not t.empty:
                tail_frames.append(t)
        tail_df = pd.concat(tail_frames) if tail_frames else probe.iloc[0:0]

    if tail_df.empty:
        return state

    new_last: Optional[pd.Timestamp] = tail_df.index.max()

    for tf in tf_list:
        rule = dataset.TIMEFRAME_RULES[tf]
        # 追記 tail を resample（小）。既存ロールアップは DataFrame のまま扱い辞書化しない
        #   （ISSUE-012: 90 万件の dict-of-dict が RSS を 618MB へ急騰させる回帰の防止）。
        new_df = dataset.resample_ohlc(tail_df, rule)
        if new_df.empty:
            continue
        path = _rollup_path(out_dir, tf)
        if path.exists():
            existing = pd.read_csv(path)
            if existing.empty:
                merged = new_df
            else:
                existing["date"] = pd.to_datetime(existing["date"])
                existing = existing.set_index("date")
                # 新規 tail の最小 period 未満は確定済み（再集計しない）＝値をそのまま温存。
                #   形成中の overlap（>= cut・高々 1 本）のみ new と groupby マージする。
                cut = new_df.index.min()
                keep = existing[existing.index < cut]
                overlap = existing[existing.index >= cut]
                union_tail = pd.concat([overlap, new_df])
                # merge_same_period と同値: open=first/high=max/low=min/close=last/volume=sum。
                #   concat 順（既存→新規）が first/last の意味（既存 open・新 close）を保証する。
                merged_tail = union_tail.groupby(level=0, sort=True).agg(
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                    volume=("volume", "sum"),
                )
                merged = pd.concat([keep, merged_tail])
        else:
            merged = new_df
        _write_rollup_df(out_dir, tf, merged)

    new_state = RollupState(
        last_processed_ts=(new_last.to_pydatetime() if new_last is not None else state.last_processed_ts)
    )
    new_state.save(out_dir)
    return new_state
