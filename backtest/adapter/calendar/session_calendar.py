"""セッションカレンダー（SessionCalendarPort 実装）。

実 MT5 突合で判明した要件: MA_Slope EA の成行が「市場閉鎖時間帯」に約定して MT5 と
乖離していた。MT5 は当該成行を `[market closed]` で拒否し、開場する次バーで約定する。
本モジュールは「約定してはならないバー」の index 集合を返す（事前計算・候補A）。

- NullCalendar: 常に空集合（既定・常時開場）。既定経路の byte-identical を担保する。
- Jp225SessionCalendar: JP225（OANDA-Japan MT5）の週次セッションを時刻ベースで近似する。
  実 MT5 02 突合（260620-02.txt）の `[market closed]` 拒否点と整合する 2 ルール:
    1) 日次プレオープン: 各日 01:00 以前（00:00–01:00）は閉鎖、01:01 開場。
       （Mon 01:00 拒否・Jan-02/05 の 01:01/01:06 約定と整合。日次ギャップ 00:00–00:59 は
        バー自体が無い）。
    2) 週末クローズ: 金曜 23:55 以降は閉鎖（Fri 23:59 拒否・金曜最終約定 23:52 と整合）。
  ギャップ検出方式は New Year 等の祝日ギャップで初回約定を誤拒否するため採らない
  （時刻ルールが実 MT5 拒否点と一致する）。

pandas/numpy 依存は本 adapter 内に閉じる（usecase/domain へ漏らさない）。Bar.time が
CSV 由来で ISO 文字列になり得る（ISSUE-016）ため曜日・時刻抽出前に Timestamp 化する。
"""
from __future__ import annotations

from typing import Any, Iterable

from backtest.usecase.ports import SessionCalendarPort

_FRIDAY = 4  # Monday=0 .. Sunday=6


class NullCalendar(SessionCalendarPort):
    """常時開場（既定）。閉鎖バー無し＝空集合を返す。"""

    def closed_bar_indices(self, bars: Iterable[Any]) -> "set[int]":
        return set()


class Jp225SessionCalendar(SessionCalendarPort):
    """JP225 週次セッション（時刻ベース近似）。

    Args:
        daily_open_minute: 日次開場の分（午前0時からの分）。既定 61=01:01 開場
            （00:00–01:00＝0..60 分は閉鎖）。
        friday_close_minute: 金曜クローズの分。既定 1435=23:55（以降は週末閉鎖）。
    """

    def __init__(
        self, *, daily_open_minute: int = 61, friday_close_minute: int = 1435
    ) -> None:
        self._daily_open_minute = int(daily_open_minute)
        self._friday_close_minute = int(friday_close_minute)

    def closed_bar_indices(self, bars: Iterable[Any]) -> "set[int]":
        import pandas as pd

        closed: set[int] = set()
        for i, bar in enumerate(bars):
            ts = pd.Timestamp(bar.time)
            mins = ts.hour * 60 + ts.minute
            # 日次プレオープン: 開場分(既定 01:01)より前は閉鎖。
            if mins < self._daily_open_minute:
                closed.add(i)
                continue
            # 週末クローズ: 金曜の指定分(既定 23:55)以降は閉鎖。
            if ts.weekday() == _FRIDAY and mins >= self._friday_close_minute:
                closed.add(i)
        return closed
