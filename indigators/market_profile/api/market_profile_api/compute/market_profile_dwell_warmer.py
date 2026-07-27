"""market_profile_dwell_warmer — dwell 日別ロールアップの事前ビルド（運用バッチ・ISSUE-133 SRP）。

ISSUE-133（SRP）: dwell の統計コア（:mod:`market_profile_dwell`）から「運用バッチ」アクター
（完了日ロールアップをディスクへ一括構築するウォーマー）を分離した。本モジュールは集計数学・serving
時キャッシュ協調を持たず、キャッシュ協調モジュール（``market_profile_dwell``）の serving 用関数
（``_day_rollup`` 等）を **call-time 参照**（``_mpd.X``）で駆動する。既存テストの monkeypatch 注入点
（``mpd.day_parquet_files`` / ``mpd._load_window_ticks`` / ``mpd._day_source_signature`` 等）を尊重する。

CLI エントリは :mod:`tools.warm_market_profile_cache`（tools/ 配下）へ分離した。
``market_profile_dwell.warm_dwell_cache`` は本実装への薄い遅延委譲として温存される（import 面不変）。
"""
from __future__ import annotations

import time as _time
from pathlib import Path as _Path
from typing import Any

import pandas as pd

from market_profile_api.compute import market_profile_dwell as _mpd
from marketdata.session_day import next_session_day_start, session_day_start


def _day_start_from_tick_path(p: Any) -> int:
    """ティック parquet パス ``.../YYYY/MM/DD/<symbol>_ticks.parquet`` から day_start(UTC 秒) を得る。"""
    parts = _Path(p).parts
    y, m, d = int(parts[-4]), int(parts[-3]), int(parts[-2])
    return int(pd.Timestamp(f"{y:04d}-{m:02d}-{d:02d}", tz="UTC").timestamp())


def warm_dwell_cache(
    symbol: str, start: Any = None, end: Any = None, now: float | None = None
) -> dict:
    """全 or 指定期間の完了日ロールアップをディスクへ一括構築する（冪等・進捗 print）。

    :func:`marketdata.tick_m1.day_parquet_files` で実在日を列挙し、各完了日を
    :func:`market_profile_dwell._day_rollup` で構築・保存する。既にディスクにある完了日はスキップ
    （冪等）。当日（未確定日）は永続化しない。一度回せば以降の全期間 dwell はディスクから高速ロードできる。

    Args:
        symbol: 実ティック symbol（例 'JP225'）。
        start/end: 期間端（None は start=2000-01-01 / end=当日。存在日のみ処理）。
        now: 完了日判定の基準時刻（既定は現在時刻。テスト注入用）。

    Returns:
        ``{built, skipped, days}``（構築数・スキップ数・列挙された実在日数）。
    """
    now_val = _time.time() if now is None else float(now)
    lo = pd.Timestamp("2000-01-01") if start is None else pd.Timestamp(start)
    hi = pd.Timestamp(now_val, unit="s").normalize() if end is None else pd.Timestamp(end)
    # ISSUE-183: 列挙ポートの契約は UNIX 秒 int（pd.Timestamp 変換は gateway 内に閉じた）。
    #   本モジュールは CLI 由来の日付表現（str / Timestamp）を受けるため、境界でのみ秒へ落とす。
    files = _mpd.day_parquet_files(int(lo.timestamp()), int(hi.timestamp()), symbol=symbol)
    built = skipped = 0
    # ISSUE-078: 実在 parquet（UTC 日）から被覆セッション日集合を導出する（同一セッションは 2 UTC 日に
    #   跨るため set で重複排除）。セッション完了判定は next_session_day_start（DST 23h/25h 対応）。
    session_days = sorted({session_day_start(_day_start_from_tick_path(p)) for p in files}
                          | {session_day_start(_day_start_from_tick_path(p) + 86399) for p in files})
    for day_start in session_days:
        if next_session_day_start(day_start) > now_val:  # 未確定の当日セッションは永続化しない。
            continue
        # ISSUE-089: スキップは「現行版として有効なキャッシュ」のみ（旧: 存在チェックのみで
        #   版数不一致の stale ファイルもスキップしていた）。署名照合込みの実ロードで検証する。
        disk, cached_sig = _mpd._load_day_rollup(_mpd._cache_path(symbol, day_start))
        # ISSUE-177（LSP）: 番兵は call-time に現在の Store から取得する（module 定数への import 時束縛は撤去）。
        if disk is not _mpd.dwell_cache_miss() and cached_sig == _mpd._day_source_signature(symbol, day_start):
            skipped += 1
            continue
        _mpd._day_rollup(symbol, day_start, None, now_val)
        built += 1
        if built % 25 == 0:
            print(f"[warm] {symbol}: {built} built / {skipped} skipped ...")
    print(f"[warm] {symbol}: done — {built} built, {skipped} skipped, {len(files)} days enumerated")
    return {"built": built, "skipped": skipped, "days": len(files)}
