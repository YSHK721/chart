"""market_profile_zp_warmer — zp 日別成果物の事前ビルド（運用バッチ・ISSUE-133 SRP）。

ISSUE-133（SRP）: zp の統計コア（:mod:`market_profile_zp`）から「運用バッチ」アクター（完了日の
mgrid＋znull をディスクへ一括構築するウォーマー）を分離した。本モジュールは集計数学・serving 時
キャッシュ協調を持たず、キャッシュ協調モジュール（``market_profile_zp``）の serving 用関数
（``_zp_day_rollup`` 等）と被覆セッション日導出（dwell warmer の ``_day_start_from_tick_path``）を
call-time 参照で駆動する。

CLI エントリは :mod:`tools.warm_market_profile_cache`（tools/ 配下）へ分離した。

ISSUE-305: ``warm_zp_cache`` の入口は本モジュールのみ（統計コア側に遅延委譲の別名は置かない）。
逆向きの委譲は 運用バッチ ⇄ 統計コア の依存循環になり、関数内 import はそれを隠すだけである。
"""
from __future__ import annotations

import time as _time
from typing import Any

import pandas as pd

from market_profile_api.compute import market_profile_zp as _zp
from market_profile_api.compute.market_profile_dwell_warmer import _day_start_from_tick_path
from marketdata.session_day import next_session_day_start, session_day_start


def warm_zp_cache(
    symbol: str, start: Any = None, end: Any = None, now: float | None = None
) -> dict:
    """全 or 指定期間の完了日 z 成果物（mgrid＋znull）をディスクへ一括構築する（冪等・進捗 print）。

    日付昇順に走査し、各完了日の mgrid → znull を構築・保存する。既にディスクにある完了日は
    スキップ（冪等）。ステップ行列 S は _hist_step_matrix 経由（mgrid ディスクヒットで高速）。
    """
    now_val = _time.time() if now is None else float(now)
    lo = pd.Timestamp("2000-01-01") if start is None else pd.Timestamp(start)
    hi = pd.Timestamp(now_val, unit="s").normalize() if end is None else pd.Timestamp(end)
    # ISSUE-183: 列挙ポートの契約は UNIX 秒 int（dwell warmer と同規則）。
    files = _zp.day_parquet_files(int(lo.timestamp()), int(hi.timestamp()), symbol=symbol)
    built = skipped = 0
    # ISSUE-078: 実在 parquet（UTC 日）から被覆セッション日集合を導出（dwell warm と同規則）。
    session_days = sorted({session_day_start(_day_start_from_tick_path(p)) for p in files}
                          | {session_day_start(_day_start_from_tick_path(p) + 86399) for p in files})
    for day_start in session_days:
        if next_session_day_start(day_start) > now_val:
            continue
        # ISSUE-137: StorePort 経由（旧 _zp._STORE）。ISSUE-182 item3: null_path のみ＝znull 役割の狭いポート。
        if _zp.zp_null_store().null_path(symbol, day_start).is_file():
            skipped += 1
            continue
        _zp._zp_day_rollup(symbol, day_start, now_val)
        built += 1
        if built % 25 == 0:
            print(f"[warm-zp] {symbol}: {built} built / {skipped} skipped ...")
    print(f"[warm-zp] {symbol}: done — {built} built, {skipped} skipped, {len(files)} days enumerated")
    return {"built": built, "skipped": skipped, "days": len(files)}
