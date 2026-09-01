"""ユースケース（usecase 層・**同パッケージのみ**に依存）。

1 周期は fetch 1 回 → absorb → ジャーナル追記 →（分が閉じたら）M1/rollup →（日が変わったら）
確定、である。順序そのものが安全性を持つ:

**検証はどの書込よりも先**
    後に回すと Fail-Stop 時に部分的に書かれた台帳が残り、「取れていないのか壊れているのか」が
    区別できなくなる。計算量検定 CX-e が「Fail-Stop 経路で全 writer 呼出 0」を固定する。

**1 周期の fetch は 1 回**
    カーソル位置や保存済み日数が増えても発行数は変わらない（CX-c）。取り直し・先読みを
    足すと、出力に使わない計算が静かに増える。

永続化ポートを置かない（YAGNI）: 差し替える相手が居ない。検定での差替は monkeypatch（既存様式）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from marketdata.mt5_ticks import cursor as cursor_rules
from marketdata.mt5_ticks import ingest, journal, m1_chain
from marketdata.mt5_ticks.cursor import Cursor
from marketdata.mt5_ticks.port import Clock, IncrementalTickSource

Row = Tuple[int, float, float]

#: 1 応答で受け取る上限行数（端末側の切り詰めは ``truncated`` で伝わる）。
DEFAULT_MAX_ROWS = 100_000
#: 日 D を確定してよくなるまでの猶予（D+1 00:00 UTC からの秒数）。
DEFAULT_GRACE_SECONDS = 300


class PollResult(NamedTuple):
    """1 周期の結果。"""

    cursor: Cursor
    received: int
    appended: int
    dropped: int
    days: "Tuple[dt.date, ...]"
    new_rows: "List[Row]"
    truncated: bool
    server: str


class PublishResult(NamedTuple):
    """表示系列への反映結果。``pending_rows`` は次の周期へ持ち越す形成中の分。"""

    bars: int
    pending_rows: "List[Row]"


@dataclass
class PollOnce:
    """UC-01: 1 周期ぶんの増分を取り、検証し、ジャーナルへ追記する。"""

    source: IncrementalTickSource
    symbol: str
    token: str
    data_dir: Any
    max_rows: int = DEFAULT_MAX_ROWS

    def __call__(self, cursor: Cursor) -> PollResult:
        from_msc, to_msc = cursor_rules.request_window(cursor)
        response = self.source.fetch(
            symbol=self.symbol, from_msc=from_msc, to_msc=to_msc, max_rows=self.max_rows
        )

        # 書込より先に検証する（Fail-Stop 時に部分的な台帳を残さない）。
        ingest.validate_rows(response.rows, from_msc=from_msc, to_msc=to_msc)
        absorbed = cursor_rules.absorb(cursor, response.rows)

        appended = 0
        days: "List[dt.date]" = []
        for day, chunk in ingest.split_by_utc_day(absorbed.new_rows):
            appended += journal.append(
                day, chunk, symbol=self.token, data_dir=self.data_dir
            )
            days.append(day)

        return PollResult(
            cursor=absorbed.next_cursor,
            received=len(response.rows),
            appended=appended,
            dropped=absorbed.dropped,
            days=tuple(days),
            new_rows=absorbed.new_rows,
            truncated=response.truncated,
            server=response.server,
        )


@dataclass
class FinalizeDay:
    """UC-02: 走査し終えた UTC 日を日別 parquet へ確定する（**1 日 1 回**）。

    確定してよいのは次のいずれかが成り立つ日だけである（設計 §5）:
    D+1 の行を観測した、または ``now >= D+1 00:00 UTC + 猶予``。当日を条件なしに確定すると
    1 周期ごとに当日全量を parquet 化することになり、当日累積に比例した無駄が生じる。
    """

    token: str
    data_dir: Any
    clock: Clock
    grace_seconds: int = DEFAULT_GRACE_SECONDS

    def is_closed(self, day: dt.date, latest_observed_day: "Optional[dt.date]" = None) -> bool:
        """``day`` を確定してよいか。"""
        if latest_observed_day is not None and latest_observed_day > day:
            return True
        boundary = dt.datetime.combine(
            day + dt.timedelta(days=1), dt.time(0, 0), tzinfo=dt.timezone.utc
        ) + dt.timedelta(seconds=self.grace_seconds)
        now = self.clock.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt.timezone.utc)
        return now >= boundary

    def __call__(
        self,
        *,
        days: "Iterable[dt.date]",
        latest_observed_day: "Optional[dt.date]" = None,
    ) -> "Dict[dt.date, str]":
        out: "Dict[dt.date, str]" = {}
        for day in sorted(set(days)):
            if not self.is_closed(day, latest_observed_day):
                continue
            out[day] = journal.finalize(day, symbol=self.token, data_dir=self.data_dir)
        return out


@dataclass
class PublishDataset:
    """UC-03: 閉じた分だけを M1 CSV へ追記し、上位足へ差分反映する。"""

    ref: str
    data_dir: Any
    clock: Clock
    update_rollups: bool = True

    def __call__(self, rows: "Sequence[Row]") -> PublishResult:
        now = self.clock.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt.timezone.utc)
        until = now.replace(second=0, microsecond=0)

        appended = m1_chain.append_m1_for_closed_minutes(
            rows, ref=self.ref, data_dir=self.data_dir, until=until
        )
        if appended.bars and self.update_rollups:
            m1_chain.update_rollups(ref=self.ref, data_dir=self.data_dir)
        return PublishResult(bars=appended.bars, pending_rows=appended.pending_rows)


@dataclass
class RestoreCursor:
    """UC-04: ジャーナルから再開点を復元する（**復元の唯一経路**）。

    復元できなければ ``None`` を返す。ここで ``now-30 分`` のような既定を作らないのは、
    「どこから取り直したか」が運用者に見えないまま欠測が埋まらない状態を避けるためである。
    コールドスタートは呼び出し側の明示（``--from``）を要求する。
    """

    token: str
    data_dir: Any

    def __call__(self, *, days: "Iterable[dt.date]") -> "Optional[Cursor]":
        for day in sorted(set(days), reverse=True):
            tail = journal.tail_rows(day, symbol=self.token, data_dir=self.data_dir)
            if tail:
                return cursor_rules.from_journal_tail(tail)
        return None
