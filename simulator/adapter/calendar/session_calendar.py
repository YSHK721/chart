"""セッションカレンダー（SessionCalendarPort 実装）。

実 MT5 突合で判明した要件: MA_Slope EA の成行が「市場閉鎖時間帯」に約定して MT5 と
乖離していた。MT5 は当該成行を `[market closed]` で拒否し、開場する次バーで約定する。
本モジュールは「約定してはならないバー」の index 集合を返す（事前計算・候補A）。

- NullCalendar: 常に空集合（既定・常時開場）。既定経路の byte-identical を担保する。
- Jp225SessionCalendar: JP225（OANDA-Japan MT5）の日次セッションを時刻ベースで近似する。
  実 MT5 02 突合（260620-02.txt）の `[market closed]` 拒否点と整合する 2 ルール:
    1) 日次プレオープン: 各日 01:00 以前（00:00–01:00）は閉鎖、01:01 開場。
       （Mon 01:00 拒否・Jan-02/05 の 01:01/01:06 約定と整合。日次ギャップ 00:00–00:59 は
        バー自体が無い）。
    2) 日次クローズ: 各日 23:59（1439 分）以降は閉鎖、23:58 まで開場
       （Fri 23:59 拒否・02-06 23:58 約定と整合。金曜固有ではなく毎日同一であることの
        実測根拠は `Jp225SessionCalendar` docstring に記す）。
  ギャップ検出方式は New Year 等の祝日ギャップで初回約定を誤拒否するため採らない
  （時刻ルールが実 MT5 拒否点と一致する）。

pandas/numpy 依存は本 adapter 内に閉じる（usecase/domain へ漏らさない）。曜日・時刻を
抽出するために `bar.time` を Timestamp 化するが、**表現差の吸収は自前で判定せず
`simulator.domain.bar_time.epoch_seconds` へ委譲する**（ISSUE-412 (A)）。

`Bar.time` の受理集合は epoch 整数（`numpy.int64` を含む）と ``numpy.datetime64`` の
2 つだけである（`domain/bar.py` の構築時契約・ISSUE-411）。かつて本 docstring は
「CSV 由来で ISO 文字列になり得る（ISSUE-016）」と述べていたが、ISSUE-411 の構築時契約に
より str の `Bar` は構築不能であり、その記述は現行仕様と一致しないため撤回する。

時間基準の未確定事項（ISSUE-414・本モジュールは主張しない）:
    下記のセッション定数は MT5 ブローカー壁時計の journal 突合から得たものである。
    一方 comma-CSV / marketdata 経路の `Bar.time` は UTC epoch である。両者の時間基準が
    一致するかは未実証であり、ISSUE-414 の裁定待ちである。ISSUE-412 の是正は
    「型の読み違いの除去」だけを行い、セッション判定の正しさを主張しない。
"""
from __future__ import annotations

from typing import Any, Iterable

from simulator.domain.bar_time import epoch_seconds
from simulator.usecase.ports import SessionCalendarPort


class NullCalendar(SessionCalendarPort):
    """常時開場（既定）。閉鎖バー無し＝空集合を返す。"""

    def closed_bar_indices(self, bars: Iterable[Any]) -> "set[int]":
        return set()


class Jp225SessionCalendar(SessionCalendarPort):
    """JP225 日次セッション（時刻ベース）。tradeable 窓 = [01:01, 23:58]。

    実 MT5 突合（260620-01/02/03 journal の `[market closed]` 拒否点）から確定した
    日次セッション境界:
      - 日次プレオープン: 00:00–01:00（0..60 分）は閉鎖、01:01 開場
        （実例 2026-01-12 / 02-02 の 01:00 拒否、01:01+ 約定）。
      - 日次クローズ: 23:59（1439 分）は閉鎖、23:58 まで開場
        （実例 2026-01-09 / 02-06 の 23:59 拒否、02-06 23:58 は約定）。
    金曜固有ではなく毎日同一（23:59 約定は全 run で 0 件・23:58 は約定あり）。
    週末（土日）はバー自体が存在しないため明示判定不要。

    Args:
        daily_open_minute: 日次開場の分（午前0時からの分）。既定 61=01:01。
        daily_close_minute: 日次クローズの分。既定 1439=23:59（以降は閉鎖）。
    """

    def __init__(
        self, *, daily_open_minute: int = 61, daily_close_minute: int = 1439
    ) -> None:
        self._daily_open_minute = int(daily_open_minute)
        self._daily_close_minute = int(daily_close_minute)

    def closed_bar_indices(self, bars: Iterable[Any]) -> "set[int]":
        import pandas as pd

        closed: set[int] = set()
        for i, bar in enumerate(bars):
            # 表現差の吸収は domain の単一ソースへ委譲する（ここで型判定を書くと
            # `Bar.time` の受理集合が本モジュールと domain の 2 か所で二重定義になる）。
            # 直接 `pd.Timestamp(bar.time)` に掛けると、epoch 整数が ns 解釈されて
            # 1970-01-01 に落ち、場中バーが全件「閉鎖」になる（ISSUE-412 (A) 実測）。
            ts = pd.Timestamp(epoch_seconds(bar.time), unit="s")
            mins = ts.hour * 60 + ts.minute
            # 日次プレオープン（01:00 以前）または日次クローズ（23:59 以降）は閉鎖。
            if mins < self._daily_open_minute or mins >= self._daily_close_minute:
                closed.add(i)
        return closed
