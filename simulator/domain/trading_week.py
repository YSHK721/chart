"""E-TradingWeek: 取引週（Value Object・詳細設計 §3.1）。

週境界・取引日集合を保持する frozen VO。週/曜日/同一立会日の判定は module-level
純関数（week_id_of / weekday_of / same_trading_day）。epoch int → UTC 規約は
report_ui/derive.py 踏襲（datetime.fromtimestamp(ts, tz=timezone.utc)）。

domain 層は外部依存ゼロ（標準ライブラリのみ・pd.Timestamp 禁止）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def week_id_of(ts: int) -> str:
    """epoch int を ISO 週 "YYYY-Www" に写す（D2・決定論）。"""
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def weekday_of(ts: int) -> int:
    """epoch int の曜日（Mon=0・UTC・D2）。"""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).weekday()


def same_trading_day(ts_a: int, ts_b: int) -> bool:
    """同一立会日（UTC 日付一致）か（D2）。"""
    da = datetime.fromtimestamp(int(ts_a), tz=timezone.utc).date()
    db = datetime.fromtimestamp(int(ts_b), tz=timezone.utc).date()
    return da == db


@dataclass(frozen=True)
class TradingWeek:
    week_id: str
    first_trading_time: int
    last_trading_time: int
    trading_times: tuple[int, ...]
    event_flag: bool = False

    def __post_init__(self) -> None:
        if len(self.trading_times) < 1:
            raise ValueError("TradingWeek: trading_times は 1 件以上")
        if self.first_trading_time != self.trading_times[0]:
            raise ValueError("first_trading_time は trading_times[0] と一致")
        if self.last_trading_time != self.trading_times[-1]:
            raise ValueError("last_trading_time は trading_times[-1] と一致")
        if list(self.trading_times) != sorted(set(self.trading_times)):
            raise ValueError("trading_times は昇順・重複なし")
