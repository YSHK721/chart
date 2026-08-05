"""rollup — 上位足の増分ロールアップ（メモリ有界化・enabler③④・rollup_builder から物理移設）。

server が 4.5M 行 / 284MB の 1 分足を全ロードして OOM する問題に対し、1 分足を二度と
全ロードしない設計の生成側を担う。1 分足を ``pd.read_csv(chunksize=...)`` でストリーム読みし、
各時間足（TF）を :func:`marketdata.resample.resample_ohlc`（marketdata の規則を再利用・再実装しない）で
集約して TF 別ロールアップ CSV（``date,open,high,low,close,volume``・loader 互換）へ書き出す。

依存方向（厳守）: 本モジュールは pandas + :mod:`marketdata.resample` + :mod:`marketdata.tail_reader`
にのみ依存し、indicator_ui を逆 import しない（marketdata の循環依存禁止・設計 §4）。

メモリ有界（厳守）:
    全行を同時に pandas へ載せない。``chunk_rows`` 単位でストリーム読みし、チャンク跨ぎの未確定
    period は carry-over（確定まで書き出さない・D-1）する。:func:`merge_same_period` の結合性が
    チャンク分割と全件 resample の数値一致を保証する。

数値一致の根拠:
    resample 規則（W-FRI/ME/5min/tz/closed/label）を再実装せず必ず
    :func:`marketdata.resample.resample_ohlc` を呼ぶ。チャンク末尾の未確定 period のみ次チャンクへ
    繰り越し、確定 period のみ書き出すことで ``stream_build`` 結果は ``resample_ohlc(全件, rule)`` と
    完全一致する。

enabler④（銘柄汎用化・§10.3 M-3）:
    ロールアップ CSV のファイル名 prefix は ``ref_prefix``（既定 ``"jp225_m1"``）引数で外部化する。
    ``ref_prefix`` は :func:`_rollup_path` と :class:`_RollupWriter` の**両所**に通し、
    :func:`stream_build` / :func:`incremental_update` から伝播する（既定値で全既存呼出は不変）。
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

# marketdata の resample 規則を再利用する（再実装しない・indicator_ui を逆 import しない）。
from marketdata import resample as _resample
from marketdata import tail_reader

# 増分更新で「state 以降の新規 1 分足」を逆シークで拾う末尾 probe 行数。--watch は毎分 ~1 行
# 追記なので十分大（≈14 日分の連続 1 分足）。probe が last_ts を内包できない長期 catch-up のみ
# 全件スキャンへフォールバックする。
_INCREMENTAL_TAIL_PROBE_ROWS = 20_000

# ロールアップ CSV の列・date 書式は marketdata.csv_schema が唯一の規則源
# （旧: tick_m1._HEADER / _DATE_FMT と手動同期）。旧属性名は import 共有で温存する。
from marketdata import csv_schema as _csv_schema

logger = logging.getLogger(__name__)

_HEADER = _csv_schema.HEADER
_DATE_FMT = _csv_schema.DATE_FMT
# ロールアップ CSV のファイル名 prefix の既定（jp225_m1 由来・<prefix>_<tf>.csv）。
# §10.3 M-3: 銘柄汎用化のため ref_prefix 引数で外部化（既定でこの値）。
_REF_PREFIX = "jp225_m1"
_STATE_FILENAME = "rollup_state.json"


def merge_same_period(prev_bar: dict[str, Any], new_bar: dict[str, Any]) -> dict[str, Any]:
    """同一 period の 2 つの partial bar を結合する（結合的）。

    open=最初の bar の open / high=max / low=min / close=後の bar の close / volume=合算。
    結合的（``merge(merge(a,b),c) == merge(a,merge(b,c))``）であり、チャンク跨ぎ carry-over の
    正しさ（D-1）の根拠となる。
    """
    merged = {
        "open": prev_bar["open"],
        "high": max(prev_bar["high"], new_bar["high"]),
        "low": min(prev_bar["low"], new_bar["low"]),
        "close": new_bar["close"],
        "volume": prev_bar["volume"] + new_bar["volume"],
    }
    # 方向内訳（up/dn）も volume と同じく合算する（両者が持つときのみ・結合的）。
    for col in _csv_schema.UPDOWN_COLUMNS:
        if col in prev_bar and col in new_bar:
            merged[col] = prev_bar[col] + new_bar[col]
    return merged


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


def rollup_timeframes() -> "tuple[str, ...]":
    """ロールアップ対象の上位足（原子 ``"1m"`` を除く全 TF）の唯一源（ISSUE-262）。

    かつて ``tools/build_tick_rollup`` と ``tools/live_tick_watch`` が同一実装を各自に持ち、
    後者の docstring は「前者と同規則」と**人手同期**を宣言していた。``tools`` パッケージが
    「ロジックの重複を持たない合成点」と宣言している以上、規則は本モジュールに置く。
    """
    from marketdata import resample

    return tuple(tf for tf in resample.TIMEFRAME_RULES if tf != "1m")


def _rollup_path(out_dir: Path, tf: str, ref_prefix: str = _REF_PREFIX) -> Path:
    """ロールアップ CSV の解決パス（``<out_dir>/<ref_prefix>_<tf>.csv``）。

    §10.3 M-3: ``ref_prefix``（既定 ``"jp225_m1"``）で銘柄を汎用化する。既定値で全既存呼出は不変。
    """
    return Path(out_dir) / f"{ref_prefix}_{tf}.csv"


def _bar_to_dict(row: pd.Series) -> dict[str, Any]:
    bar = {
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
    }
    # 方向内訳（up/dn）は tick 由来データだけが持つ任意列。持つときだけ運ぶ（無い素材は不変）。
    for col in _csv_schema.UPDOWN_COLUMNS:
        if col in row.index:
            bar[col] = float(row[col])
    return bar


def _header_for_bars(bars: "dict[Any, dict[str, Any]]") -> list[str]:
    """bars の内容からロールアップ CSV ヘッダを決める（up/dn を持つときだけ末尾へ足す）。"""
    sample = next(iter(bars.values()), {})
    return _csv_schema.header_for([c for c in (*_csv_schema.OHLCV_COLUMNS,
                                               *_csv_schema.UPDOWN_COLUMNS) if c in sample])


def _merge_agg(columns: Any) -> "dict[Any, str]":
    """既存 CSV と新規 tail をマージするときの列別集約規則（実在列から導出・単一定義）。

    規則は :func:`marketdata.resample.resample_ohlc` と同一（OHLC=first/max/min/last、
    :data:`marketdata.csv_schema.SUM_COLUMNS` は sum、その他は last）。列名を呼び出し側へ
    直書きしないことで、csv_schema へ列が増えても本経路が列を落とさない。
    """
    fixed = {"open": "first", "high": "max", "low": "min", "close": "last"}
    agg: "dict[Any, str]" = {}
    for col in columns:
        lc = str(col).lower()
        if lc in fixed:
            agg[col] = fixed[lc]
        elif lc in _csv_schema.SUM_COLUMNS:
            agg[col] = "sum"
        else:
            agg[col] = "last"
    return agg


def _header_of(path: Path) -> "list[str] | None":
    """ロールアップ CSV の 1 行目（ヘッダ）を列名リストで返す（不在・空は ``None``）。"""
    try:
        with open(path, "r", newline="", encoding="utf-8") as fh:
            line = fh.readline()
    except OSError:
        return None
    line = line.strip()
    return line.split(",") if line else None


def _bar_to_csv_row(period: Any, bar: dict[str, Any]) -> list[Any]:
    """(period, bar) を ロールアップ CSV の 1 行（loader 互換）へ整形する（単一定義）。

    ``_write_rollup``（全件一括書き）と :class:`_RollupWriter`（逐次 flush）で同一フォーマットを
    共用し、両経路の出力 CSV をバイト一致させるための行整形の単一真実源。
    """
    row = [
        pd.Timestamp(period).strftime(_DATE_FMT),
        bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"],
    ]
    row.extend(bar[c] for c in _csv_schema.UPDOWN_COLUMNS if c in bar)
    return row


def _write_rollup(
    out_dir: Path, tf: str, bars: dict[Any, dict[str, Any]], ref_prefix: str = _REF_PREFIX
) -> None:
    """period→bar の辞書を date 昇順でロールアップ CSV へ**原子的に**書き出す（loader 互換形式）。

    原子化（🔴）: 同一ディレクトリの一時ファイルへ書き切ってから ``os.replace`` で確定パスへ
    swap する。``--watch`` が毎分この全書き直しを行うため、書込中の crash/OOM-kill で確定パスに
    部分 CSV が残ると、cold-start の server が torn-read フォールバック不能のまま不完全データを
    配信する。tmp→replace で確定パスは「完全な新 CSV」か「旧 CSV」のいずれかに限定する。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    import csv as _csv

    final = _rollup_path(out_dir, tf, ref_prefix)
    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), prefix=final.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(_header_for_bars(bars))
            for period in sorted(bars):
                w.writerow(_bar_to_csv_row(period, bars[period]))
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_rollup_df(
    out_dir: Path, tf: str, df: pd.DataFrame, ref_prefix: str = _REF_PREFIX
) -> None:
    """date-index OHLCV DataFrame を date 昇順でロールアップ CSV へ**原子的に**書く（増分経路）。

    増分更新（ISSUE-012）の memory-bounded 経路。``_write_rollup``（辞書版）と同じ tmp→``os.replace``
    の原子化で確定パスを「完全な新 CSV」か「旧 CSV」のいずれかに限定する。出力列・date 書式
    （``_DATE_FMT``）・ヘッダは loader 互換（:mod:`marketdata.csv_schema`）で揃える。90 万件規模でも
    辞書化せず pandas の to_csv をストリーム書きするため RSS は DataFrame 1〜2 個分に有界化する。

    出力列は **実在する列から導出**する（``_bar_to_csv_row`` / ``_merge_agg`` と同じ規約・ISSUE-258）。
    かつてここは ``["open","high","low","close","volume"]`` を直書きしており、csv_schema へ up/dn が
    増えたとき**本経路だけが列を落とした**。しかも一度 6 列で書かれるとヘッダ不一致で次回も全件
    rewrite へ落ちるため自己修復せず、方向内訳が恒久的に失われる（消費側の tickvol_updown は値を
    捏造せず KeyError で落ちる）。列の決定は csv_schema 1 点に閉じること。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = _rollup_path(out_dir, tf, ref_prefix)
    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), prefix=final.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        cols = [c for c in (*_csv_schema.OHLCV_COLUMNS, *_csv_schema.UPDOWN_COLUMNS)
                if c in df.columns]
        out = df[cols].sort_index()
        out = out.copy()
        out.index = pd.DatetimeIndex(out.index).strftime(_DATE_FMT)
        out.index.name = "date"
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            out.to_csv(fh, header=list(out.columns), index_label=_HEADER[0])
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

    §10.3 M-3: ``ref_prefix``（既定 ``"jp225_m1"``）を ``__init__`` に追加し確定パス名へ反映する。
    """

    def __init__(self, out_dir: Path, tf: str, ref_prefix: str = _REF_PREFIX) -> None:
        import csv as _csv

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._final = _rollup_path(out_dir, tf, ref_prefix)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(out_dir), prefix=self._final.name + ".", suffix=".tmp"
        )
        self._tmp: Optional[Path] = Path(tmp_name)
        self._fh = os.fdopen(fd, "w", newline="", encoding="utf-8")
        self._w = _csv.writer(self._fh)
        # ヘッダは最初の bar が来るまで書かない（up/dn を持つ素材かは bar を見ないと決まらない）。
        #   1 行も書かれなければ commit 時に既定ヘッダを書く＝従来の空 CSV と同一。
        self._header_written = False
        self._committed = False

    def _ensure_header(self, bar: "dict[str, Any] | None") -> None:
        if self._header_written:
            return
        self._w.writerow(_header_for_bars({0: bar} if bar is not None else {}))
        self._header_written = True

    def write(self, period: Any, bar: dict[str, Any]) -> None:
        """確定済み 1 バーを 1 行 flush する（呼び出しは date 昇順であること）。"""
        self._ensure_header(bar)
        self._w.writerow(_bar_to_csv_row(period, bar))

    def commit(self) -> None:
        """tmp を閉じ確定パスへ原子スワップする（成功時のみ呼ぶ）。"""
        if self._committed:
            return
        self._ensure_header(None)
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


def _resample_chunk(chunk: pd.DataFrame, tf: str) -> "OrderedBars":
    """チャンク（date index 化済み）を tf で resample し period→bar の順序辞書を返す。

    ISSUE-078: 規則解決は :func:`marketdata.resample.resample_ohlc_tf`（1D/1W/1M はセッション日
    集計・日中足は UTC floor）へ単一化する。
    """
    resampled = _resample.resample_ohlc_tf(chunk, tf)
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


def _rollup_last_period(path: Path) -> Optional[pd.Timestamp]:
    """既存ロールアップの末尾バーの period（最終行のみ逆シーク読み）。空・不在なら None。"""
    if not path.exists():
        return None
    tail = tail_reader.read_tail(path, 1)
    if tail.empty:
        return None
    return pd.Timestamp(tail.index[-1])


def _last_data_line_offset(path: Path) -> int:
    """ロールアップ CSV の「最終データ行」が始まるバイト位置を逆シークで求める（truncate 起点）。

    末尾の改行を 1 つ無視し、その手前の改行の次バイト＝最終データ行の先頭。データ行が 1 本
    （ヘッダ＋1 行）のときはヘッダ直後（＝ヘッダ行末改行の次）を返す。
    """
    block = 64 * 1024
    with open(path, "rb") as f:
        f.seek(0, io.SEEK_END)
        size = f.tell()
        if size == 0:
            return 0
        buf = b""
        cur = size
        while cur > 0:
            step = min(block, cur)
            cur -= step
            f.seek(cur)
            buf = f.read(step) + buf
            stripped = buf[:-1] if buf.endswith(b"\n") else buf
            idx = stripped.rfind(b"\n")
            if idx != -1:
                return cur + idx + 1
        return 0


def _truncate_append_bars(path: Path, offset: int, bars: "OrderedBars") -> None:
    """``offset`` で切り詰め、period→bar を昇順で追記する（末尾だけ書く＝O(新規)・原子的でない）。

    最終データ行（形成中バー）を ``offset`` から切り落とし、再計算した末尾バー群（形成中の上書き
    ＋新規確定 append）を ``_bar_to_csv_row`` 形式で書く。履歴（prefix）は一切触らない。
    書込中 crash の窓では末尾が欠けうるが、(1) state は全 TF 成功後に保存するため次 tick で
    再処理され、(2) 形成中バーは probe から**再計算**（マージでなく上書き）するため再処理が冪等、
    (3) ロールアップは 1 分足から再生成可能、で復元できる。
    """
    import csv as _csv

    with open(path, "r+", newline="", encoding="utf-8") as fh:
        fh.seek(offset)
        fh.truncate()
        w = _csv.writer(fh)
        for period in sorted(bars):
            w.writerow(_bar_to_csv_row(period, bars[period]))
        fh.flush()
        os.fsync(fh.fileno())


def _resample_suffix(probe: pd.DataFrame, tf: str, since_period: pd.Timestamp) -> "OrderedBars":
    """probe 全体を tf で resample し、``since_period`` 以降の period→bar（完全バー）を返す。

    probe が ``since_period`` の期間 **UTC 始端**（:func:`marketdata.resample.period_utc_start`）を
    内包する前提。since_period 以降の各 period の 1 分足は probe に連続して含まれるため、その
    resample 結果は形成中バーも含め完全（partial でない）＝そのまま上書きしてよい（ISSUE-078:
    セッション tf はラベルがブローカー暦日のため、被覆判定は period_utc_start で行うこと）。
    """
    resampled = _resample.resample_ohlc_tf(probe, tf)
    suffix = resampled[resampled.index >= since_period]
    bars: "OrderedBars" = {}
    for period, row in suffix.iterrows():
        bars[period] = _bar_to_dict(row)
    return bars


def stream_build(
    m1_csv_path: Path,
    tf_list: Iterable[str],
    out_dir: Path,
    ref_prefix: str = _REF_PREFIX,
    chunk_rows: int = 500_000,
) -> "RollupState":
    """1 分足を chunk 単位でストリーム読みし、各 TF をロールアップ CSV へ書き出す（メモリ有界）。

    チャンク跨ぎの未確定（最終）period は carry-over し、確定するまで書き出さない（D-1）。
    全行を同時に DataFrame 化しない（``pd.read_csv(chunksize=chunk_rows)``）。

    §10.3 M-3: ``ref_prefix``（既定 ``"jp225_m1"``）を ``_RollupWriter`` へ伝播し出力ファイル名を
    銘柄汎用化する（既定値で全既存呼出は不変）。
    """
    tf_list = list(tf_list)
    m1_csv_path = Path(m1_csv_path)
    # TF ごとに確定済み period バーを逐次 flush する writer（確定バーを蓄積しない＝メモリ有界化）。
    #   常駐は 1 chunk ＋ TF ごとの carry-over 境界バー 1 本のみ（巨大期間でも確定バーが嵩まない）。
    writer_by_tf: dict[str, _RollupWriter] = {
        tf: _RollupWriter(out_dir, tf, ref_prefix) for tf in tf_list
    }
    # TF ごとに「未確定（carry-over 中）の最終 period→bar」を 1 本だけ保持する。
    pending_by_tf: dict[str, Optional[tuple[Any, dict[str, Any]]]] = {tf: None for tf in tf_list}

    last_ts: Optional[pd.Timestamp] = None

    try:
        for raw_chunk in pd.read_csv(m1_csv_path, chunksize=chunk_rows):
            chunk = _index_chunk(raw_chunk)
            if not chunk.empty:
                last_ts = chunk.index.max()
            for tf in tf_list:
                chunk_bars = _resample_chunk(chunk, tf)
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
    ref_prefix: str = _REF_PREFIX,
) -> "RollupState":
    """state 以降の追記 tail のみ読み、各 TF ロールアップ末尾へマージする（増分・メモリ有界）。

    state 不在（None）は初回として :func:`stream_build` へフォールバックする。同一 period は
    上書き（形成中バー更新）、新規 period は append（period クローズ＝確定 append）する。

    §10.3 M-3: ``ref_prefix``（既定 ``"jp225_m1"``）を :func:`_rollup_path` /
    :func:`_write_rollup_df` / :func:`stream_build` へ伝播し出力ファイル名を銘柄汎用化する。
    """
    tf_list = list(tf_list)
    if state is None:
        return stream_build(m1_csv_path, tf_list, out_dir, ref_prefix)

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
    probe_covers = not probe.empty

    for tf in tf_list:
        path = _rollup_path(out_dir, tf, ref_prefix)
        # ---- O(新規) 速い経路: 末尾だけ truncate+append（過去確定足を read/write しない）----
        #   probe が「既存末尾 period の期間始端」を内包すれば、形成中バーを probe から再計算
        #   （上書き＝冪等）でき、ロールアップ全体（5m≈64MB）の read/write を避けられる。
        last_period = _rollup_last_period(path)
        # ISSUE-078: セッション tf のラベルはブローカー暦日＝probe（UTC index）との被覆判定は
        #   期間の UTC 始端（period_utc_start）で行う（ラベル直接比較は最大 24h 過大評価し、
        #   形成中バー前半を欠落させ得る）。日中足は period_utc_start がラベル素通し＝従来同値。
        if (
            last_period is not None
            and probe_covers
            and probe.index.min() <= _resample.period_utc_start(tf, last_period)
        ):
            suffix = _resample_suffix(probe, tf, last_period)
            if suffix:
                # 追記する行の列構成が既存ヘッダと一致するときだけ速い経路を使う。食い違ったまま
                #   追記すると CSV が恒久的に読めなくなる（ヘッダ 6 列のファイルへ 8 列行が入り、
                #   以後その tf の /candles・rollup 読取・ライブ watch が全部落ちる＝実障害）。
                #   不一致は「列が増えた直後」に起きるので、全件 rewrite へ落として**ヘッダごと
                #   書き直す**（次回以降は一致して速い経路へ戻る＝自己修復）。
                if _header_of(path) == _header_for_bars(suffix):
                    offset = _last_data_line_offset(path)
                    _truncate_append_bars(path, offset, suffix)
                    continue
                logger.warning(
                    "ロールアップ CSV の列構成が変わりました（%s）。追記でなく全件 rewrite で"
                    "ヘッダごと書き直します。", path.name,
                )
            else:
                continue
        # ---- フォールバック（全件 rewrite）: probe 不足（1M 等）・ファイル不在・空 ----
        # 追記 tail を resample（小）。既存ロールアップは DataFrame のまま扱い辞書化しない
        #   （ISSUE-012: 90 万件の dict-of-dict が RSS を 618MB へ急騰させる回帰の防止）。
        new_df = _resample.resample_ohlc_tf(tail_df, tf)
        if new_df.empty:
            continue
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
                #   集約対象の列は **実在する列から導出**する（列名をここに直書きしない）。直書きは
                #   csv_schema へ列（up/dn）が増えたときに本経路だけ列を落とし、同じファイルへ
                #   速い経路（_bar_to_csv_row＝全列）が追記した瞬間にヘッダと行の列数が食い違って
                #   CSV を恒久破壊する（実際に jp225_tick_1M.csv がこれで壊れた）。
                merged_tail = union_tail.groupby(level=0, sort=True).agg(
                    _merge_agg(union_tail.columns)
                )
                merged = pd.concat([keep, merged_tail])
        else:
            merged = new_df
        _write_rollup_df(out_dir, tf, merged, ref_prefix)

    new_state = RollupState(
        last_processed_ts=(new_last.to_pydatetime() if new_last is not None else state.last_processed_ts)
    )
    new_state.save(out_dir)
    return new_state
