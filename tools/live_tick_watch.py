#!/usr/bin/env python3
"""jp225_tick 系のライブ watch CLI（既存アクターの合成のみ）。

チャート表示データセット ``jp225_tick``（tick 由来）へライブでデータを供給し続けるための
薄い Composition Root。新しいコアロジックは最小限に留め、既存アクターを **in-process import
で合成**する（``tools.build_tick_rollup`` の様式を参照実装として踏襲）。

毎ループの実体（:func:`update_once`）:
  1. refresh : 当日（＋日跨ぎ直後は前日）の tick parquet を **全量再取得**して原子スワップする
               （:func:`refresh_days` が対象日・:func:`refresh_day_parquet` が取得＋原子上書き）。
  2. m1      : tick 由来 M1（``jp225_tick_m1.csv``）を増分追記する。形成中の分バー
               （``floor(now, "min")`` 以降）は ``until`` で除外し、確定値のみ書き込む
               （:func:`marketdata.tick_m1.append_m1_from_ticks`）。
  3. rollup  : M1 を上位足（5m..1M）へ差分更新する（``ref_prefix="jp225_tick"``・
               ``marketdata.rollup.incremental_update``・専用サブ dir へ隔離）。

起動時 1 回（:func:`catch_up`）: 既存 tick tree の最新取得日の翌日〜**昨日**までの丸日を
``simulator.tools.fetch_ticks_ymd.run`` で追い付き取得する（当日は毎分の full-refresh が担当）。

データ保全（重要）:
  - tick parquet の再取得は同一ディレクトリの一時ファイルへ書いてから ``os.replace`` で原子
    スワップする（reader は torn な中間状態を観測しない）。取得 0 件の日は **既存 parquet を
    温存**して上書きしない（休場・一過性障害の防御）。
  - 派生物（M1・ロールアップ）は新 ref ``jp225_tick`` 専用ファイルとして生成し、既存
    ``jp225_m1.csv`` 系には触れない（読取＋新規追加のみ）。

クリーンアーキ / 依存方向:
  - 本モジュールは最上位の合成点（tools 層）であり marketdata / simulator.tools /
    tools.build_tick_rollup に依存してよい（逆は無い）。ベンダ（dukascopy）は直接 import せず
    既存アクター経由。外部・重い呼び出しは monkeypatch 可能なモジュール関数
    （``_fetch_day`` / ``_fetch_ticks_run`` / ``_compute_acquire_range`` / ``_append_m1`` /
    ``_rollup_update``）へ隔離し、遅延 import で副作用を実行時に限定する。
  - 「現在時刻」は ``_utc_now`` に隔離する（Date.now 相当をハードコードしない・テスト決定性）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import pandas as pd

LOG = logging.getLogger("live_tick_watch")

# 出力 ref（tick 由来）・全期間起点は build_tick_rollup と単一定義を共有する（値ドリフト防止）。
# 旧: _DEFAULT_FULL_START をここで再定義し「一致させること」コメントで人手同期していた（SRP 破れ）。
from tools.build_tick_rollup import REF, _DEFAULT_FULL_START  # noqa: E402
# ポーリング間隔の下限（秒）。過剰ポーリング抑止（下回る指定は argparse エラー）。
MIN_INTERVAL_SECONDS = 30
DEFAULT_INTERVAL_SECONDS = 60


# --------------------------------------------------------------------------- #
# パス基点（marketdata.paths.DATA_DIR を単一基点とする・遅延 import）
# --------------------------------------------------------------------------- #
def _data_dir() -> Path:
    from marketdata.paths import DATA_DIR  # 遅延: import 副作用を実行時に限定

    return Path(DATA_DIR)


def _rollup_timeframes() -> Tuple[str, ...]:
    """ロールアップ対象の上位足（原子 "1m" を除く全 TF・build_tick_rollup._rollup_timeframes と同規則）。"""
    from marketdata import resample

    return tuple(tf for tf in resample.TIMEFRAME_RULES if tf != "1m")


# --------------------------------------------------------------------------- #
# 「現在時刻」の隔離（テストで monkeypatch）
# --------------------------------------------------------------------------- #
def _utc_now() -> dt.datetime:
    """現在時刻を UTC ナイーブ datetime で返す（M1 index は naive UTC・tz を焼き込まない）。"""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# 既存アクターの薄いラッパ（monkeypatch 差替点・遅延 import で副作用を限定）
# --------------------------------------------------------------------------- #
def _fetch_day(day: dt.date, next_day: dt.date) -> "pd.DataFrame | None":
    """``[day, next_day)``（UTC）の JP225 tick を取得する（DukascopyTickSource へ委譲）。"""
    from marketdata import JP225, DukascopyTickSource  # 遅延: ここで dukascopy_python を要求

    src = DukascopyTickSource(instrument=JP225)
    start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    end = dt.datetime(next_day.year, next_day.month, next_day.day, tzinfo=dt.timezone.utc)
    return src.fetch_ticks(start, end)


def _fetch_ticks_run(start: dt.datetime, end: dt.datetime, root: Path) -> int:
    """丸日追い付きの委譲先（既存日 skip・休場は empty マーカー）。"""
    from simulator.tools.fetch_ticks_ymd import run as _run

    return _run(start, end, root)


def _compute_acquire_range(
    ticks_root: Path, today: dt.date, *, full_start: dt.date, end: dt.date
) -> Optional[Tuple[dt.datetime, dt.datetime]]:
    """取得すべき ``[start, end)``（UTC・半開）を算出する（build_tick_rollup の規則を再利用）。"""
    from tools.build_tick_rollup import compute_acquire_range

    return compute_acquire_range(Path(ticks_root), today, full_start=full_start, end=end)


def _latest_tick_day(ticks_root: Path) -> Optional[dt.date]:
    """既存 tick tree の最新取得日（UTC）を返す。空 tree（取得済み 0 日）は ``None``。

    :func:`tools.acquire_marketdata.next_tick_start_day`（最新日の翌日）から逆算する
    （日付走査ロジックを二重定義しない）。
    """
    from tools.acquire_marketdata import PipelineError as _AcqError
    from tools.acquire_marketdata import next_tick_start_day

    try:
        return (next_tick_start_day(Path(ticks_root)) - dt.timedelta(days=1)).date()
    except _AcqError:
        return None


def _append_m1(start: str, end: str, until: pd.Timestamp, *, data_dir: Path) -> Path:
    """tick 由来 M1 を増分追記する（形成中分バーは until で除外・ref=jp225_tick）。"""
    from marketdata.tick_m1 import append_m1_from_ticks

    return append_m1_from_ticks(start, end, until=until, ref=REF, data_dir=data_dir)


def _rollup_update(data_dir: Path):
    """tick 由来 M1 を上位足へ差分更新する（ref_prefix=jp225_tick・専用サブ dir へ隔離）。"""
    from marketdata.rollup import RollupState, incremental_update
    from marketdata.tick_m1 import m1_csv_path

    out_dir = Path(data_dir) / "rollups" / REF
    out_dir.mkdir(parents=True, exist_ok=True)
    m1_path = m1_csv_path(ref=REF, data_dir=data_dir)
    state = RollupState.load(out_dir)
    return incremental_update(m1_path, state, _rollup_timeframes(), out_dir, ref_prefix=REF)


# --------------------------------------------------------------------------- #
# 純粋ロジック: 再取得対象日
# --------------------------------------------------------------------------- #
def refresh_days(now: dt.datetime, interval: int) -> List[dt.date]:
    """再取得すべき UTC 日の昇順一意リストを返す（純粋）。

    ``{date(now - 2*interval秒), date(now)}`` の一意集合を昇順で返す。日跨ぎ直後（now が
    00:00〜00:0X）は前日を最終リフレッシュ対象に含め、日末尾の取りこぼしを防ぐ。
    """
    earlier = now - dt.timedelta(seconds=2 * interval)
    return sorted({earlier.date(), now.date()})


# --------------------------------------------------------------------------- #
# 当日 tick 全量再取得（原子スワップ・空温存）
# --------------------------------------------------------------------------- #
def refresh_day_parquet(
    day: dt.date, data_dir: Path, fetch_fn: Optional[Callable] = None
) -> int:
    """``day`` の tick を全量再取得し、日別 parquet を原子スワップする。取得 tick 数を返す。

    ``fetch_fn(day, day+1)``（既定は :func:`_fetch_day`）で当日全 tick を取得する。空（休場・
    一過性障害）なら **既存 parquet を温存**して 0 を返す（上書きしない）。非空なら同一
    ディレクトリの一時ファイルへ ``to_parquet(index=False)``（fetch_ticks_ymd と同一保存形式＝
    raw ネイティブ列のまま）→ ``os.chmod 0o644`` → ``os.replace`` で原子スワップする。
    パスは :func:`marketdata.tick_m1.day_parquet_path`（tick tree レイアウトの単一権威）で解決し、
    書込成功時は同日の ``.empty`` マーカー（fetch_ticks_ymd の休場印）を除去して整合させる。
    """
    from marketdata.tick_m1 import day_parquet_path  # 遅延: レイアウト権威は marketdata に単一化

    if fetch_fn is None:
        fetch_fn = _fetch_day
    df = fetch_fn(day, day + dt.timedelta(days=1))
    if df is None or len(df) == 0:
        return 0  # 空取得は既存 parquet を温存（休場・一過性障害の防御）。

    out = day_parquet_path(day, data_dir=data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 原子化: 一時ファイルへ書き→完了時に os.replace で原子スワップ（reader は torn を観測しない）。
    fd, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=out.name + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        df.to_parquet(tmp, index=False)  # fetch_ticks_ymd と同一保存形式（raw ネイティブ列）。
        os.chmod(tmp, 0o644)  # mkstemp の 0600 を fetch_ticks_ymd の書込相当へ揃える。
        os.replace(tmp, out)  # 原子スワップ（同一 FS rename）。
    except BaseException:
        tmp.unlink(missing_ok=True)  # 失敗時は旧 parquet を温存（部分書きで上書きしない）。
        raise
    # parquet が実在するのに休場マーカーが残ると状態不整合（reader は parquet 優先だが混在を残さない）。
    out.with_suffix(".empty").unlink(missing_ok=True)
    return len(df)


# --------------------------------------------------------------------------- #
# 起動時 1 回の丸日追い付き
# --------------------------------------------------------------------------- #
def catch_up(data_dir: Path, today: dt.date, *, full_start: dt.date) -> int:
    """起動時 1 回の丸日追い付き（既存最新日の翌日〜**昨日**）。取得 tick 数を返す。

    まず既存 tick tree の最新取得日（:func:`_latest_tick_day`）が過去日なら、その日を
    :func:`refresh_day_parquet` で**上書き再取得**する（日中に取得された部分日 parquet の
    自己修復。fetch_ticks_ymd は既存日を skip するためここでしか埋まらない。当日は毎分の
    full-refresh が担当するので対象外・空 tree は no-op）。
    次に :func:`_compute_acquire_range` で範囲を算出し（``end=today-1``＝昨日まで）、
    None なら no-op（0）、あれば :func:`_fetch_ticks_run`
    （``simulator.tools.fetch_ticks_ymd.run``・既存日 skip・休場は empty マーカー）へ委譲する。
    """
    from marketdata.tick_m1 import tick_root  # 遅延: tick tree 基点の単一権威

    ticks_root = tick_root(data_dir)
    latest = _latest_tick_day(ticks_root)
    if latest is not None and latest < today:
        refreshed = refresh_day_parquet(latest, data_dir)
        LOG.info("catch_up: 既存最新日 %s を上書き再取得（%s ticks・部分日の自己修復）", latest, refreshed)
    rng = _compute_acquire_range(
        ticks_root, today, full_start=full_start, end=today - dt.timedelta(days=1)
    )
    if rng is None:
        LOG.info("catch_up: 取得対象期間なし（昨日まで取得済み）")
        return 0
    start, end = rng
    LOG.info("catch_up: %s..%s 追い付き取得（既存日は skip）", start.date(), (end - dt.timedelta(days=1)).date())
    total = _fetch_ticks_run(start, end, Path(ticks_root))
    LOG.info("catch_up: %s ticks 取得", total)
    return total


# --------------------------------------------------------------------------- #
# 1 ループの実体（refresh → m1 → rollup）
# --------------------------------------------------------------------------- #
def update_once(
    now: dt.datetime, data_dir: Path, *, interval: int, full_start: dt.date = _DEFAULT_FULL_START
) -> None:
    """1 ループを実行する（当日 tick 再取得 → M1 増分追記 → rollups 差分更新）。

    (a) :func:`refresh_days` の各日を :func:`refresh_day_parquet` で全量再取得し原子スワップ、
    (b) :func:`_append_m1` で ``full_start``〜``today+1`` を増分追記（``until=floor(now, "min")``
        で形成中分バーを除外）、(c) :func:`_rollup_update` で上位足を差分更新する。

    m1 の ``start`` に当日でなく ``full_start`` を渡すのは、実際の追記窓の決定を
    ``append_m1_from_ticks`` の resume 規則（``eff_start = max(既存最終バー日, start)``＝
    最終バー日から再読込・``index > 最終 date`` のみ追記）へ委ねるため。既存 M1 が数日前で
    停止していても catch_up 済みの丸日 parquet から欠損日を自己修復でき（当日 start だと
    その間の日が永久欠落する）、定常運転では最終バー日≒当日のため読むのは当日 parquet のみ。
    """
    data_dir = Path(data_dir)
    days = refresh_days(now, interval)
    for day in days:
        refresh_day_parquet(day, data_dir)
    end = now.date() + dt.timedelta(days=1)  # today+1（半開の m1 集計終端）。
    until = pd.Timestamp(now).floor("min")  # 形成中分バー（>= until）を確定値として書かない。
    _append_m1(full_start.isoformat(), end.isoformat(), until, data_dir=data_dir)
    _rollup_update(data_dir)




# --------------------------------------------------------------------------- #
# ストリーミング取得（ISSUE-161 根治・参照実装 prototype_260707-01 _poll_loop 踏襲）
# --------------------------------------------------------------------------- #
# 参照実装のセマンティクス（絶対遵守）: 増分カーソル（厳密 > cursor）・直列 1 接続・
#   失敗は指数バックオフ（interval→×2・上限 60s）・連続 8 失敗でサーキットブレーカ（600s 停止）。
#   取得間隔 5 秒は参照実装で実機実証済み（feed lag 3.8〜5.5s・fetch ~1.2s・枯渇なし）。
STREAM_DEFAULT_INTERVAL = 5.0
STREAM_MIN_INTERVAL = 2.0            # 参照実装より短くしない下限（過剰ポーリング抑止）
_STREAM_BREAK_ERRORS = 8             # 参照実装: 連続失敗数の閾値
_STREAM_PAUSE_SECONDS = 600.0        # 参照実装: サーキットブレーカ停止秒
_STREAM_BACKOFF_MAX = 60.0           # 参照実装: バックオフ上限
_STREAM_RECONCILE_SECONDS = 1800.0   # 当日全量再取得による自己修復周期（増分ドリフトの恒久補正）
# M1 確定の猶予秒。分境界直後は feed 側遅延（実測最大 5.5s）＋ポーリング間隔ぶんの末尾 tick が
#   未着でありうる。即時確定すると閉じたバーが欠けたまま焼かれ、resume 規則（追記のみ）では
#   二度と直らない。参照実装の固定遅延と同じ根拠（5.5 + 5 + 余裕）で 12 秒待ってから確定する。
_STREAM_M1_GRACE_SECONDS = 12.0

# 日別 parquet の正準列（fetch_ticks_ymd / refresh_day_parquet と同一の raw ネイティブ列）。
_TICK_COLUMNS = ["timestamp", "bidPrice", "askPrice", "bidVolume", "askVolume"]


def _rows_to_frame(rows: Sequence[tuple]) -> pd.DataFrame:
    """増分 API 行 (unix_ms, bid, ask, bidVol, askVol) を日別 parquet と同一スキーマの DataFrame へ。"""
    df = pd.DataFrame(rows, columns=_TICK_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).astype("datetime64[ms, UTC]")
    for c in _TICK_COLUMNS[1:]:
        df[c] = df[c].astype("float64")
    # 出来高の単位正規化: 増分 API は日次取得の 1e6 倍の生値を返す（実測: 同時間帯で
    #   12000.0 vs 0.012）。日別 parquet の単位（日次取得系）へ揃える。M1 合成は volume 列を
    #   使わない（ティック数集計・tick_m1 参照）ため計算結果には無影響＝ファイル整合のみ。
    df["bidVolume"] = df["bidVolume"] / 1_000_000.0
    df["askVolume"] = df["askVolume"] / 1_000_000.0
    return df


def _write_day_parquet(day: dt.date, df: pd.DataFrame, data_dir: Path) -> None:
    """日別 parquet を原子スワップで書く（refresh_day_parquet の書込部と同一手順）。"""
    from marketdata.tick_m1 import day_parquet_path

    out = day_parquet_path(day, data_dir=data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=out.name + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        df.to_parquet(tmp, index=False)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    out.with_suffix(".empty").unlink(missing_ok=True)


def _load_day_frame(day: dt.date, data_dir: Path) -> "pd.DataFrame | None":
    """日別 parquet を読み込む（無ければ None）。"""
    from marketdata.tick_m1 import day_parquet_path

    p = day_parquet_path(day, data_dir=data_dir)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def _cursor_ms_of(df: "pd.DataFrame | None", now: dt.datetime) -> int:
    """増分カーソル初期値: 既存 df の最終 tick ms。無ければ now-30 分（参照実装と同じ catch-up 窓）。"""
    if df is not None and len(df):
        return int(pd.Timestamp(df["timestamp"].iloc[-1]).timestamp() * 1000)
    return int((pd.Timestamp(now, tz="UTC").timestamp() - 30 * 60) * 1000)


def _chain_m1_rollup(now: dt.datetime, data_dir: Path, full_start: dt.date) -> None:
    """分確定の連鎖処理: M1 増分追記（形成中分バー除外）→ rollups 差分更新（update_once と同一）。

    until は ``floor(now - 猶予12s)``: 分境界直後の末尾 tick 未着（feed 遅延）を待ってから
    確定する（欠けた確定バーを焼かない・ISSUE-161 ストリーミング化の正確性ガード）。
    """
    end = now.date() + dt.timedelta(days=1)
    until = pd.Timestamp(now - dt.timedelta(seconds=_STREAM_M1_GRACE_SECONDS)).floor("min")
    _append_m1(full_start.isoformat(), end.isoformat(), until, data_dir=data_dir)
    _rollup_update(data_dir)


def stream_loop(
    data_dir: Path,
    *,
    interval: float = STREAM_DEFAULT_INTERVAL,
    full_start: dt.date = _DEFAULT_FULL_START,
) -> None:
    """増分カーソルストリーミング常駐ループ（参照実装踏襲・ISSUE-161 根治）。

    5 秒ごとに増分 tick（差分数 KB・公式ライブウィジェット同一経路）を取得して当日 parquet へ
    原子追記し、分境界を跨いだ時だけ M1 増分追記＋rollups 差分更新を連鎖実行する。
    30 分ごとに当日全量再取得（refresh_day_parquet）で自己修復する（増分ドリフト・欠落の恒久補正。
    再取得が空を返した場合は既存 parquet 温存＝増分で貯めた当日データを失わない）。
    日跨ぎでは前日を全量再取得で確定し、当日バッファを新規に始める。
    """
    from marketdata.dukascopy_source import fetch_ticks_since

    now = _utc_now()
    today = now.date()
    refresh_day_parquet(today, data_dir)                 # スキーマ正の seed（空なら温存）
    day_df = _load_day_frame(today, data_dir)
    cursor = _cursor_ms_of(day_df, now)
    _chain_m1_rollup(now, data_dir, full_start)          # 起動直後に確定分を追い付き
    last_minute = pd.Timestamp(now - dt.timedelta(seconds=_STREAM_M1_GRACE_SECONDS)).floor("min")
    last_reconcile = time.monotonic()
    backoff = float(interval)
    errors_in_row = 0
    paused_until = 0.0
    LOG.info("stream: 開始 interval=%.1fs cursor=%s", interval, cursor)
    while True:
        if time.monotonic() < paused_until:
            time.sleep(1.0)
            continue
        t0 = time.monotonic()
        try:
            now = _utc_now()
            if now.date() != today:                      # 日跨ぎ: 前日確定→当日バッファ新規
                refresh_day_parquet(today, data_dir)
                today = now.date()
                day_df = _load_day_frame(today, data_dir)
            rows = fetch_ticks_since(cursor, with_volumes=True)
            if rows:
                inc = _rows_to_frame(rows)
                day_df = inc if day_df is None or not len(day_df) else pd.concat(
                    [day_df, inc], ignore_index=True)
                day_df["timestamp"] = day_df["timestamp"].astype("datetime64[ms, UTC]")
                _write_day_parquet(today, day_df, data_dir)
                cursor = int(rows[-1][0])
            minute = pd.Timestamp(now - dt.timedelta(seconds=_STREAM_M1_GRACE_SECONDS)).floor("min")
            if minute > last_minute:                     # 分確定（猶予後）の瞬間だけ M1/rollup を連鎖
                _chain_m1_rollup(now, data_dir, full_start)
                last_minute = minute
            if time.monotonic() - last_reconcile >= _STREAM_RECONCILE_SECONDS:
                refresh_day_parquet(today, data_dir)     # 自己修復（空なら温存）
                day_df = _load_day_frame(today, data_dir)
                cursor = max(cursor, _cursor_ms_of(day_df, now))
                last_reconcile = time.monotonic()
            backoff = float(interval)
            errors_in_row = 0
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 — 参照実装: 一過性障害はバックオフして継続
            errors_in_row += 1
            LOG.warning("stream: 取得失敗 %d/%d: %s: %s",
                        errors_in_row, _STREAM_BREAK_ERRORS, type(exc).__name__, exc)
            if errors_in_row >= _STREAM_BREAK_ERRORS:
                paused_until = time.monotonic() + _STREAM_PAUSE_SECONDS
                errors_in_row = 0
                LOG.warning("stream: サーキットブレーカ発動（%.0fs 停止）", _STREAM_PAUSE_SECONDS)
            backoff = min(backoff * 2, _STREAM_BACKOFF_MAX)
        time.sleep(max(0.0, backoff - (time.monotonic() - t0)))

# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _interval_seconds(value: str) -> int:
    """``--interval`` の型（下限 ``MIN_INTERVAL_SECONDS`` 秒のフロア）。下回る指定は argparse エラー。"""
    seconds = int(value)
    if seconds < MIN_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(
            f"--interval は {MIN_INTERVAL_SECONDS} 秒以上を指定してください（指定値: {seconds}）"
        )
    return seconds


def _parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI 引数パーサを構築する（テストから純粋に引数検証できるよう factory 化）。"""
    p = argparse.ArgumentParser(
        prog="live_tick_watch",
        description="jp225_tick 系のライブ watch（毎分 当日tick全量再取得→M1増分→rollups差分更新）。",
    )
    p.add_argument(
        "--interval",
        type=_interval_seconds,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"ポーリング間隔秒（既定 {DEFAULT_INTERVAL_SECONDS} / 下限 {MIN_INTERVAL_SECONDS}）",
    )
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="データ基点（既定 marketdata.paths.DATA_DIR）。tick 読書き・ロールアップ出力は"
        "すべてこの dir 由来（ticks=<dir>/ticks・rollup=<dir>/rollups/jp225_tick）",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="1 ループのみ実行して終了（手動検証用・catch_up→update_once を 1 回）",
    )
    p.add_argument(
        "--full-start",
        type=_parse_date,
        default=_DEFAULT_FULL_START,
        help=f"catch_up の全期間起点 YYYY-MM-DD（既定 {_DEFAULT_FULL_START.isoformat()}）",
    )
    p.add_argument(
        "--stream",
        action="store_true",
        help="増分カーソルストリーミング（参照実装 prototype_260707-01 準拠・ISSUE-161 根治）。"
        "5 秒周期の差分取得＋分確定ごとの M1/rollups 連鎖（--interval は無視される）",
    )
    p.add_argument(
        "--stream-interval",
        type=float,
        default=STREAM_DEFAULT_INTERVAL,
        help=f"ストリーミング取得間隔秒（既定 {STREAM_DEFAULT_INTERVAL} / 下限 {STREAM_MIN_INTERVAL}）",
    )
    p.add_argument("--quiet", action="store_true", help="ログを抑制する")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")

    data_dir = args.data_dir if args.data_dir is not None else _data_dir()
    today = _utc_now().date()

    # 起動時 1 回の丸日追い付き（昨日まで）。
    catch_up(data_dir, today, full_start=args.full_start)

    if args.stream:
        if args.stream_interval < STREAM_MIN_INTERVAL:
            raise SystemExit(
                f"--stream-interval は {STREAM_MIN_INTERVAL} 秒以上を指定してください"
                f"（指定値: {args.stream_interval}・過剰ポーリング抑止）"
            )
        stream_loop(data_dir, interval=args.stream_interval, full_start=args.full_start)
        return 0

    if args.once:
        update_once(_utc_now(), data_dir, interval=args.interval, full_start=args.full_start)
        return 0

    # 継続ポーリング: 共有層の run_watch（例外はログして次インターバル継続・KeyboardInterrupt 正常終了）。
    from tools.watch_loop import run_watch

    def _update() -> None:
        update_once(_utc_now(), data_dir, interval=args.interval, full_start=args.full_start)

    LOG.info("live_tick_watch 開始（interval=%ds・ref=%s）", args.interval, REF)
    return run_watch(_update, interval=args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
