"""tf メタ・tick ref・期間始端・now 解決の単一情報源（ISSUE-087 🔴-1/🔴-2）。

移設元: indigators/indicator_ui/api/adapter/compute/forming_bar.py の純関数群。
market_profile_api と indicator_ui api の両方が本モジュールを同格に参照することで、
MP→indicator_ui の裸パッケージ依存（sys.path 注入前提の横断結合）を排する。
規則源: 周期集合・floor 可否は :data:`marketdata.resample.TIMEFRAME_RULES`、
1D セッション始端は :mod:`marketdata.session_day`（二重定義しない）。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import pandas as pd

from marketdata.resample import TIMEFRAME_RULES
from marketdata.session_day import session_day_start

# 形成中バー/tf-period を供給する datasetRef（ティック由来＝ticks parquet を持つ）。
TICK_REFS = frozenset({"jp225_tick"})

# カレンダー周期（W-FRI/ME）は単純 floor で期間始端を表せない。
NON_FLOORABLE_TF = frozenset({"1W", "1M"})

# tf → バー秒長（名目値）。1W=7日・1M=30日名目（カレンダー tf の窓幅・表示計算用。
#   厳密な期間境界は resample/session_day のラベル規約が担う＝本表を境界計算に使わない）。
TF_BAR_SEC: "dict[str, int]" = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1D": 86400, "1W": 604800, "1M": 2592000,
}

# プロセス起動時刻（resolve_now_unix のデモ時計の経過基準）。
_BOOT_MONOTONIC = time.monotonic()


def is_tick_ref(ref: Any) -> bool:
    """形成中バー/tf-period 供給対象の ref か（ティック由来）。"""
    return ref in TICK_REFS


def floor_freq(tf: Any) -> Optional[str]:
    """tf の pandas floor freq を TIMEFRAME_RULES から導出する（1W/1M・未知は None）。"""
    if tf in NON_FLOORABLE_TF or tf not in TIMEFRAME_RULES:
        return None
    rule = TIMEFRAME_RULES[tf]
    return "min" if rule is None else rule


def is_supported_timeframe(tf: Any) -> bool:
    """固定周期（floor 可能）tf か（1W/1M・未知は False）。"""
    return floor_freq(tf) is not None


def period_start_unix(now_unix: int, tf: str) -> int:
    """現在期間の始端 UNIX 秒（ISSUE-078: '1D' はセッション日始端・日中足は UTC floor）。"""
    if tf == "1D":
        return session_day_start(int(now_unix))
    start = pd.Timestamp(int(now_unix), unit="s").floor(floor_freq(tf))  # naive UTC
    return int(start.value // 1_000_000_000)


def resolve_now_unix(override: Any = None) -> int:
    """基準時刻 now（UNIX 秒・UTC）を解決する（時刻取得の単一注入点）。

    優先順位: ①override（int・bool 除外）②env FORMING_DEMO_NOW="<base>[:<speed>]"
    （デモ時計＝base から実経過×speed）③実 UTC 現在。
    """
    if isinstance(override, int) and not isinstance(override, bool):
        return override
    demo = os.environ.get("FORMING_DEMO_NOW")
    if demo:
        base, _, sp = demo.partition(":")
        try:
            speed = float(sp) if sp else 1.0
            return int(float(base) + (time.monotonic() - _BOOT_MONOTONIC) * speed)
        except ValueError:
            logging.getLogger(__name__).warning(
                "FORMING_DEMO_NOW の形式が不正です: %r（実時刻にフォールバック）", demo)
    return int(time.time())
