#!/usr/bin/env python3
"""marketdata.tick_m1 — 生ティック parquet → M1 原子 OHLC CSV（上位足ロールアップの素材）。

Dukascopy 生ティック（日別 parquet）を mid=(bid+ask)/2 基準・UTC で 1 分足へ集計し、
``<ref>_m1.csv``（``date,open,high,low,close,volume`` 形式・:mod:`marketdata.rollup` 互換）を
出力する。以降の上位足（5m/1h/1D …）は :mod:`marketdata.rollup`（:mod:`marketdata.resample`
の規則）が本 M1 を素材に生成する。これによりチャートの足も足内更新も「同じティック
（mid・UTC）」由来となり、書き変わりなく整合する。

責務分離（重要）:
    本モジュールは **ticks → M1（原子）の素材生成**のみを担う。上位足ロールアップ生成は
    :mod:`marketdata.rollup` の責務であり本モジュールは行わない（rollup を逆 import しない）。

価格・volume の意味:
    - price は mid=(bid+ask)/2（約定値を持たない quote feed のため・``ingest_ticks`` の
      last=mid 規約と整合する mid 採用）。
    - volume はその 1 分の **ティック数**（``size``・float）。出来高ではなく更新密度を表す。

データ保全（重要）:
    物理パスは :data:`marketdata.paths.DATA_DIR` に一本化する（ハードコード禁止）。ティックは
    ``DATA_DIR/ticks/YYYY/MM/DD/<symbol>_ticks.parquet``、出力は ``DATA_DIR/<ref>_m1.csv``。
    既存 candle CSV（``jp225_m1.csv`` 等）には触れず、新規 ref を新規出力するのみ
    （読み取り＋新規追加・既存データへ波及させない）。

依存方向: 本モジュールは pandas + :mod:`marketdata.paths` にのみ依存する（indicator_ui を
逆 import しない・marketdata の循環依存禁止）。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import pandas as pd

from marketdata.paths import DATA_DIR

# ロールアップ互換の M1 CSV 列・date 書式は marketdata.csv_schema が唯一の規則源
# （旧: rollup._HEADER / _DATE_FMT と手動同期）。旧属性名は import 共有で温存する。
from marketdata import csv_schema as _csv_schema

_HEADER = _csv_schema.HEADER
_OHLCV_COLUMNS = _csv_schema.OHLCV_COLUMNS  # _HEADER から date を除いた値列。
_DATE_FMT = _csv_schema.DATE_FMT
# 集計に要する生ティックの必須列（ingest.RAW_COLUMNS の price 部分集合）。
_TICK_COLUMNS = ["timestamp", "bidPrice", "askPrice"]
# 既定の銘柄・出力 ref（試作 prep_tick_rollup と一致: <ref>_m1.csv = jp225_tick_m1.csv）。
_DEFAULT_SYMBOL = "JP225"
_DEFAULT_REF = "jp225_tick"
# ref の許容文字（パス区切り・".." を排除し DATA_DIR 外書込／既存データ破壊を防ぐ）。
_REF_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_ref(ref: str) -> None:
    """``ref`` を単純なファイル名トークンに限定する（fail-fast・データ保全）。

    パス区切りや ``..`` を含む値は ``<ref>_m1.csv`` を介して DATA_DIR 外への書込や既存 CSV の
    上書き（破壊）を招くため拒否する。
    """
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise ValueError(
            f"ref は [A-Za-z0-9_-] のみ可: {ref!r}（パス区切り・'..' を含めない・データ保全）。"
        )


def _ts_and_mid(ticks: pd.DataFrame) -> "tuple[pd.Series, pd.Series]":
    """生ティック frame から ``(timestamp(naive UTC), mid)`` を返す共通前処理（mid/tz の単一規則源）。

    timestamp は tz-aware なら UTC 揃え後に tz を剥がし naive datetime64 へ（全 UTC＝値不変）。
    mid=(bidPrice+askPrice)/2。:func:`ticks_to_m1`（M1 集計）と :func:`forming_bar_from_ticks`
    （形成中バー）が共有し、「同一ソース＝書き変わり無し」を構造で保証する（規則の二重定義を避ける）。
    """
    ts = pd.to_datetime(ticks["timestamp"])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    mid = (ticks["bidPrice"].astype("float64") + ticks["askPrice"].astype("float64")) / 2.0
    return ts, mid


def ticks_to_m1(ticks: pd.DataFrame) -> pd.DataFrame:
    """生ティック DataFrame を M1 OHLC（mid 基準・UTC 分床）へ集計する純粋関数。

    入力は ``timestamp``（tz-aware/naive いずれも可・tz-aware は UTC へ揃える）, ``bidPrice``,
    ``askPrice`` 列を持つ。mid=(bid+ask)/2 を price とし、UTC naive の分床（``floor("min")``）で
    groupby して open=最初/high=最大/low=最小/close=最終、volume=その分のティック数（float）を
    返す。open/close を時刻順に確定させるため、集計前に ``timestamp`` 昇順へ安定ソートする。

    戻り値は ``date`` を index（名前 ``"date"``・``DatetimeIndex`` 昇順）に持つ OHLCV DataFrame。
    入力が空なら空（列のみ）を返す。必須列を欠く場合は :class:`ValueError`（fail-fast）。
    """
    missing = [c for c in _TICK_COLUMNS if c not in ticks.columns]
    if missing:
        raise ValueError(
            f"tick frame に必須列がありません: {missing}（必須 {_TICK_COLUMNS}）。"
        )
    if ticks.empty:
        empty_idx = pd.DatetimeIndex([], name="date")
        return pd.DataFrame(
            {c: pd.Series(dtype="float64") for c in ("open", "high", "low", "close", "volume")},
            index=empty_idx,
        )

    ts, mid = _ts_and_mid(ticks)

    # 時刻順を保証してから分床で groupby（open=最初/close=最終を時刻基準で確定）。
    work = pd.DataFrame({"ts": ts.to_numpy(), "mid": mid.to_numpy()})
    work = work.sort_values("ts", kind="stable", ignore_index=True)
    work["date"] = work["ts"].dt.floor("min")
    g = work.groupby("date", sort=True)["mid"]
    m1 = pd.DataFrame(
        {
            "open": g.first(),
            "high": g.max(),
            "low": g.min(),
            "close": g.last(),
            "volume": g.size().astype("float64"),  # その 1 分のティック数。
        }
    )
    m1.index.name = "date"
    return m1


def tick_root(data_dir: Any = DATA_DIR) -> Path:
    """ティック parquet の基点（``<DATA_DIR>/ticks``）。"""
    return Path(data_dir) / "ticks"


def m1_csv_path(ref: str = _DEFAULT_REF, data_dir: Any = DATA_DIR) -> Path:
    """M1 出力 CSV の解決パス（``<DATA_DIR>/<ref>_m1.csv``・rollup の ref_prefix と整合）。"""
    return Path(data_dir) / f"{ref}_m1.csv"


def day_parquet_path(day: Any, *, symbol: str = _DEFAULT_SYMBOL, data_dir: Any = DATA_DIR) -> Path:
    """``day`` の日別ティック parquet の正準パスを返す（実在チェックはしない）。

    tick tree レイアウト ``<DATA_DIR>/ticks/YYYY/MM/DD/<symbol>_ticks.parquet`` の単一権威
    （reader: :func:`day_parquet_files` / writer: tools.live_tick_watch が共用し、レイアウト
    変更を本所 1 箇所に閉じる）。
    """
    d = pd.Timestamp(day)
    return (
        tick_root(data_dir)
        / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        / f"{symbol}_ticks.parquet"
    )


def day_parquet_files(
    start: Any, end: Any, *, symbol: str = _DEFAULT_SYMBOL, data_dir: Any = DATA_DIR
) -> List[Path]:
    """``[start, end]``（両端含む・日次）の実在する日別ティック parquet を昇順で列挙する。

    パスは :func:`day_parquet_path`（レイアウト単一権威）で解決し、実在するものだけ
    返す（欠損日はスキップ・休場日対応）。
    """
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out: List[Path] = []
    d = s
    while d <= e:
        p = day_parquet_path(d, symbol=symbol, data_dir=data_dir)
        if p.is_file():
            out.append(p)
        d += pd.Timedelta(days=1)
    return out


def _format_m1_for_csv(m1: pd.DataFrame) -> pd.DataFrame:
    """date-index OHLCV を loader 互換 CSV 行へ整形する**単一規則源**（date=``_DATE_FMT`` 文字列）。

    全構築（:func:`_write_m1_csv`）と増分追記（:func:`_append_m1_csv`）の双方がこれを呼び、列射影・
    date 書式・昇順を一致させる（書式の二重定義による drift を防ぐ）。
    """
    out = m1[_OHLCV_COLUMNS].sort_index().copy()
    out.index = pd.DatetimeIndex(out.index).strftime(_DATE_FMT)
    out.index.name = _HEADER[0]
    return out


def _write_m1_csv(m1: pd.DataFrame, path: Path) -> None:
    """date-index OHLCV を loader 互換 CSV（``_HEADER`` / ``_DATE_FMT``）へ**原子的に**書く。

    rollup._write_rollup_df と同じ tmp→``os.replace`` の原子化で、確定パスを「完全な新 CSV」か
    「旧 CSV」のいずれかに限定する（書き掛けの破損 CSV を残さない）。
    """
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        out = _format_m1_for_csv(m1)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            out.to_csv(fh, header=_HEADER[1:], index_label=_HEADER[0])
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _drop_forming_bars(m1: pd.DataFrame, until: Any) -> pd.DataFrame:
    """``until`` 指定時、``index >= until`` の分バー（形成中）を除外する共通フィルタ。

    ``until=None`` は素通し（従来出力を byte 不変に保つ）。用途は「形成中の分バー
    （``floor(now, "min")`` 以降）を確定値として書き込まない」こと。:func:`build_m1_from_ticks`
    と :func:`append_m1_from_ticks` の双方が共有し、除外規則の二重定義を避ける。
    """
    if until is None:
        return m1
    return m1[m1.index < pd.Timestamp(until)]


def build_m1_from_ticks(
    start: Any,
    end: Any,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    ref: str = _DEFAULT_REF,
    data_dir: Any = DATA_DIR,
    until: Any = None,
) -> Path:
    """``[start, end]`` の日別ティック parquet を読み、M1 CSV を生成して出力パスを返す。

    対象期間に実在する parquet が 1 つも無ければ :class:`FileNotFoundError`（fail-fast・
    暗黙の空出力を作らない）。出力は ``<data_dir>/<ref>_m1.csv``。

    メモリ有界（marketdata の中核不変条件・rollup と同方針）: 全ティックを一括ロードせず
    **日別 parquet を 1 ファイルずつ** :func:`ticks_to_m1` で M1（数十〜数百倍に縮約）へ集約し、
    小さな日別 M1 のみを連結する。ティック parquet は UTC 日で partition されるため分バーが
    ファイルを跨がず、結果は全件一括集計と**数値同一**（RSS は 1 日分ティックに有界化）。

    ``until``（省略可・:class:`pd.Timestamp` 互換）を指定すると、生成する M1 バーのうち
    ``index >= until`` の行を除外する（用途: 形成中の分バー＝``floor(now, "min")`` 以降を確定値
    として書き込まない）。``until=None``（既定）は従来出力と完全一致（byte 不変）。
    """
    _validate_ref(ref)
    files = day_parquet_files(start, end, symbol=symbol, data_dir=data_dir)
    if not files:
        raise FileNotFoundError(
            f"ティック parquet が見つかりません（{start}..{end} / {tick_root(data_dir)} / "
            f"symbol={symbol}）。"
        )
    daily_m1: List[pd.DataFrame] = []
    for p in files:
        m1_day = ticks_to_m1(pd.read_parquet(p, columns=_TICK_COLUMNS))
        if not m1_day.empty:
            daily_m1.append(m1_day)
    if daily_m1:
        m1 = pd.concat(daily_m1).sort_index()
    else:
        # parquet は在るが全日空（0 行）。ヘッダのみの空 M1 を出力する。
        m1 = ticks_to_m1(pd.DataFrame({c: [] for c in _TICK_COLUMNS}))
    m1 = _drop_forming_bars(m1, until)  # 形成中分バー（>= until）を確定値として書かない。
    out_path = m1_csv_path(ref=ref, data_dir=data_dir)
    _write_m1_csv(m1, out_path)
    return out_path


def _read_last_m1_row(out_path: Any) -> "pd.DataFrame | None":
    """既存 M1 CSV の末尾 1 行（date index・OHLCV 列）を逆シーク読みで返す。不在/空は ``None``。

    :mod:`marketdata.tail_reader` で末尾 1 行のみ読むためメモリ有界（全読みしない）。
    """
    from marketdata import tail_reader

    p = Path(out_path)
    if not p.is_file():
        return None
    tail = tail_reader.read_tail(p, 1)
    return None if tail.empty else tail


def last_m1_date(out_path: Any) -> "pd.Timestamp | None":
    """既存 M1 CSV の最終バー ``date``（末尾行）。不在/空（ヘッダのみ）は ``None``。メモリ有界。"""
    tail = _read_last_m1_row(out_path)
    return None if tail is None else pd.Timestamp(tail.index[-1])


def _is_healthy_m1_row(tail: pd.DataFrame) -> bool:
    """末尾 1 行が健全か（date 解釈可・OHLCV 列が揃い NaN を含まない）。

    非原子追記がクラッシュ/ディスクフルで途中失敗すると末尾に torn/部分行（列欠落・NaN）が残りうる。
    これを検出して全構築フォールバック（原子的）で自己修復するための健全性判定。
    """
    if pd.isna(tail.index[-1]):
        return False
    if any(c not in tail.columns for c in _OHLCV_COLUMNS):
        return False
    return not bool(tail.iloc[-1][_OHLCV_COLUMNS].isna().any())


def _append_m1_csv(m1_new: pd.DataFrame, path: Path) -> None:
    """新規 M1 行を既存 CSV の**末尾へ追記**する（ヘッダ無し・``_DATE_FMT``・date 昇順）。

    既存行は読み込まず（メモリ有界）末尾追記のみ行う。呼び出し側が ``m1_new`` の全 index を既存
    最終 date より後に保証するため、追記後も date 昇順（loader 前提）が保たれる。

    原子性（注意・:func:`_write_m1_csv` との非対称）: 末尾追記は tmp→``os.replace`` の原子化を持たず、
    クラッシュ時に末尾へ torn 行を残しうる。その torn 行は次回 :func:`append_m1_from_ticks` の
    :func:`_is_healthy_m1_row` 検出で全構築フォールバックされ自己修復する（無検出の永続破損を避ける）。
    """
    out = _format_m1_for_csv(m1_new)
    with open(Path(path), "a", newline="", encoding="utf-8") as fh:
        out.to_csv(fh, header=False, index_label=_HEADER[0])


def append_m1_from_ticks(
    start: Any,
    end: Any,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    ref: str = _DEFAULT_REF,
    data_dir: Any = DATA_DIR,
    until: Any = None,
) -> Path:
    """既存 M1 CSV に「最終バー日以降の不足分」だけを集計して**追記**する（増分・メモリ有界・自己修復）。

    既存 CSV が不在/空、または末尾行が不健全（torn/部分書込み）なら :func:`build_m1_from_ticks`
    （原子的全構築）へフォールバックして自己修復する。健全時は **最終バー日（当日）以降**を再読込し、
    ``index > 最終 date`` の行だけ追記する。ティックは UTC 日で partition され分が日を跨がないため、
    完成済みの最終日は空追記（冪等 no-op）、途中までしか書けていない日は欠損分のみ追記され自己修復する。
    結果は全構築と一致する（過去確定日の再計算は不要）。

    ``until``（省略可・:class:`pd.Timestamp` 互換）を指定すると、追記する M1 バーのうち
    ``index >= until`` の行を除外する（形成中の分バー＝``floor(now, "min")`` 以降を確定値として
    書き込まない）。フォールバック先の :func:`build_m1_from_ticks` へも同じ ``until`` を伝播する。
    ``until=None``（既定）は従来出力と完全一致（byte 不変）。

    前提（重要）: 取得は前方追記（resume）である。過去日への遡及バックフィル（既存最終日より前の
    欠損日を後から追加）は本増分では取り込めない。その場合は :func:`build_m1_from_ticks` で全再構築する。
    """
    _validate_ref(ref)
    out_path = m1_csv_path(ref=ref, data_dir=data_dir)
    tail = _read_last_m1_row(out_path)
    if tail is None or not _is_healthy_m1_row(tail):
        # 初回（M1 不在/空）or 末尾 torn 行 → 原子的全構築で（再）生成し自己修復。
        return build_m1_from_ticks(
            start, end, symbol=symbol, ref=ref, data_dir=data_dir, until=until
        )

    last_date = pd.Timestamp(tail.index[-1])
    # 最終バー日（当日）から再読込し index > last_date のみ追記する。完成日は冪等 no-op、
    # 部分日は欠損分のみ自己修復（要求 start がそれより後ろならそれを尊重）。
    resume_start = last_date.normalize()
    eff_start = max(resume_start, pd.Timestamp(start))
    files = day_parquet_files(eff_start, end, symbol=symbol, data_dir=data_dir)
    if not files:
        return out_path  # 追記すべき新しい日は無い。

    daily_m1: List[pd.DataFrame] = []
    for p in files:
        m1_day = ticks_to_m1(pd.read_parquet(p, columns=_TICK_COLUMNS))
        if not m1_day.empty:
            daily_m1.append(m1_day)
    if not daily_m1:
        return out_path
    m1_new = pd.concat(daily_m1).sort_index()
    m1_new = m1_new[m1_new.index > last_date]  # 厳密に既存最終 date より後のみ追記（重複防止）。
    m1_new = _drop_forming_bars(m1_new, until)  # 形成中分バー（>= until）を確定値として書かない。
    if m1_new.empty:
        return out_path
    _append_m1_csv(m1_new, out_path)
    return out_path


def forming_bar_from_ticks(
    start_unix: int,
    end_unix: int,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    data_dir: Any = DATA_DIR,
) -> "dict | None":
    """``[start_unix, end_unix)`` の実ティックから**形成中バー**（mid OHLCV・1本）を返す。

    ライブの足内更新へ「形成中（in-progress）バー」を供給するための純粋集計。期間内の実ティックを
    mid=(bid+ask)/2 で集計し、open=最初/high=最大/low=最小/close=最終、volume=ティック数、
    ``time``=期間始端（``start_unix``）の 1 本（lightweight-charts 形）を返す。期間内にティックが
    無ければ ``None``。

    引数は UNIX 秒（UTC・整数・半開 ``[start, end)``）。日 partition（``ticks/YYYY/MM/DD``）を跨ぐ
    場合は該当日 parquet を順に読む（通常 intraday は単一日）。メモリ有界（対象期間の日 parquet の
    mid 列のみ・全期間ロードしない）。集計規則（mid・open=最初/close=最終・volume=ティック数）は
    :func:`ticks_to_m1` と一致する（同一ソース由来＝書き変わり無し）。
    """
    s = pd.Timestamp(start_unix, unit="s")  # naive UTC wall time
    e = pd.Timestamp(end_unix, unit="s")
    if e <= s:
        return None
    files = day_parquet_files(s.normalize(), e.normalize(), symbol=symbol, data_dir=data_dir)
    if not files:
        return None
    frames = [pd.read_parquet(p, columns=_TICK_COLUMNS) for p in files]
    ticks = pd.concat(frames, ignore_index=True)
    ts, mid = _ts_and_mid(ticks)
    work = pd.DataFrame({"ts": ts.to_numpy(), "mid": mid.to_numpy()})
    work = work[(work["ts"] >= s) & (work["ts"] < e)].sort_values("ts", kind="stable")
    if work.empty:
        return None
    m = work["mid"]
    return {
        "time": int(start_unix),
        "open": float(m.iloc[0]),
        "high": float(m.max()),
        "low": float(m.min()),
        "close": float(m.iloc[-1]),
        "volume": float(len(m)),
    }


def main(argv: List[str] | None = None) -> None:
    """CLI: ``python -m marketdata.tick_m1 [START] [END] [SYMBOL] [REF]``。

    START 既定 ``2025-01-01``、END 既定は本日（UTC）。試作 prep_tick_rollup の CLI を踏襲する。
    """
    args = sys.argv[1:] if argv is None else list(argv)
    start = args[0] if len(args) > 0 else "2025-01-01"
    end = args[1] if len(args) > 1 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbol = args[2] if len(args) > 2 else _DEFAULT_SYMBOL
    ref = args[3] if len(args) > 3 else _DEFAULT_REF

    files = day_parquet_files(start, end, symbol=symbol)
    print(f"範囲 {start}..{end}  symbol={symbol}  ティック日数: {len(files)}", flush=True)
    if not files:
        print(f"ティック parquet が見つかりません（{tick_root()}）", flush=True)
        return
    out_path = build_m1_from_ticks(start, end, symbol=symbol, ref=ref)
    m1 = pd.read_csv(out_path)
    if len(m1):
        print(
            f"M1バー: {len(m1):,}  ({m1['date'].iloc[0]} .. {m1['date'].iloc[-1]})  -> {out_path}",
            flush=True,
        )
    else:
        print(f"M1バー: 0  -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
