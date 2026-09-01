"""検定用の Fake / Spy（test support・**本番経路から import されない**）。

置き場所を検定側でなくパッケージ内にする理由:
    同じ Fake を domain・usecase・adapter の各検定が使う。検定ファイルごとに書くと、
    「MT5 はこう振る舞う」という仮定が複数箇所に散り、片方だけ直った瞬間に検定同士が
    別の世界を仮定し始める。

:class:`FakeTickSource` が「テープ」を持つ理由:
    端末は要求のたびに「その時点で持っている全ティックのうち窓に入るもの」を返す。窓の下端を
    含む以上、**境界 ms の行は必ず重複して返る**。この重複を検定側で人為的に作ると、
    重複の量が実装ではなく検定の都合で決まってしまう。テープから窓で切り出すことで、
    重複は本物と同じ理由で発生する（計算量検定 CX-a が測るのはこの重複だけである）。

依存宣言: :mod:`marketdata.mt5_ticks` 下位のみ。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, List, Optional, Sequence, Tuple

from marketdata.mt5_ticks import wire

Row = Tuple[int, float, float]

#: 実測のサーバ名（ISSUE-446）。トークン生成の入力として使う。
DEFAULT_SERVER = "OANDA-Japan MT5 Live"


class FixedClock:
    """止まった時計。日跨ぎ・確定条件を検定で再現するために使う。"""

    def __init__(self, now: dt.datetime):
        self._now = now

    def now(self) -> dt.datetime:
        return self._now

    def advance(self, **delta: Any) -> "FixedClock":
        """時計を進める（同じ実体を返すので DI 済みの相手にも反映される）。"""
        self._now = self._now + dt.timedelta(**delta)
        return self


class FakeTickSource:
    """端末が持つ「テープ」から窓で切り出して返す供給元。

    ``tape`` を検定側で伸ばすと、次の ``fetch`` からその行が見えるようになる
    （時間が進んでティックが増える様子を再現する）。
    """

    def __init__(
        self,
        tape: "Sequence[Row]" = (),
        *,
        server: str = DEFAULT_SERVER,
        ignore_window: bool = False,
    ):
        self.tape: "List[Row]" = list(tape)
        self.server = server
        #: 窓を無視して全件返す（契約違反の応答を再現するため・E-2 の検定に使う）。
        self.ignore_window = ignore_window
        #: 発行された要求の記録（``fetch`` 1 回につき 1 件）。
        self.calls: "List[dict]" = []

    def fetch(
        self, *, symbol: str, from_msc: int, to_msc: "Optional[int]", max_rows: int
    ) -> wire.TickResponse:
        self.calls.append(
            {"symbol": symbol, "from_msc": from_msc, "to_msc": to_msc, "max_rows": max_rows}
        )
        if self.ignore_window:
            rows = list(self.tape)
        else:
            rows = [
                r for r in self.tape
                if r[0] >= from_msc and (to_msc is None or r[0] <= to_msc)
            ]
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return wire.TickResponse(
            rows=rows,
            count=len(rows),
            latest_msc=rows[-1][0] if rows else int(from_msc),
            truncated=truncated,
            server=self.server,
        )


class FailingTickSource:
    """常に失敗する供給元（Fail-Stop 経路で書込 0 を確かめる・CX-e）。"""

    def __init__(self, error: BaseException):
        self.error = error
        self.calls: "List[dict]" = []

    def fetch(
        self, *, symbol: str, from_msc: int, to_msc: "Optional[int]", max_rows: int
    ) -> wire.TickResponse:
        self.calls.append(
            {"symbol": symbol, "from_msc": from_msc, "to_msc": to_msc, "max_rows": max_rows}
        )
        raise self.error


class CallSpy:
    """呼び出しを数えて委譲する Test Spy。

    「発行した計算 − 出力に使った計算 = 0」を表明するために使う。**回数そのものを期待値に
    焼き込まない**（それをすると浪費が仕様へ昇格する）。固定するのは無駄の不在である。
    """

    def __init__(self, target: "Optional[Callable]" = None, *, measure: "Optional[Callable]" = None):
        self.target = target
        #: 呼び出しごとの計測値（既定は 1 件 1）。``measure`` で行数などに変えられる。
        self.measurements: "List[Any]" = []
        self.calls: "List[Tuple[tuple, dict]]" = []
        self._measure = measure

    @property
    def count(self) -> int:
        """呼び出し回数。"""
        return len(self.calls)

    @property
    def total(self) -> int:
        """計測値の合計（例: 直列化した行数の合計）。"""
        return sum(self.measurements)

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.measurements.append(1 if self._measure is None else self._measure(*args, **kwargs))
        if self.target is None:
            return None
        return self.target(*args, **kwargs)
