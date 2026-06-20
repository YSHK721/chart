"""E-Bar: 価格バー（Value Object・CLEAN_ARCH §4）。

不変条件（DESIGN §3）:
    low <= min(open, close) <= max(open, close) <= high
    spread >= 0
違反時は :class:`OHLCInvalidError` を送出する。振る舞いなし（不変データ）。

時刻型は ``numpy.datetime64`` または epoch int を想定する（``pd.Timestamp`` 禁止）。
domain 層は numpy のみ依存可。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simulator.domain.exceptions import OHLCInvalidError


@dataclass(frozen=True)
class Bar:
    """1 本の価格バー。生成時に OHLC 整合と spread 非負を検証する。"""

    time: Any  # numpy.datetime64 または epoch int
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: int

    def __post_init__(self) -> None:
        lo_body = min(self.open, self.close)
        hi_body = max(self.open, self.close)
        # 不変条件: low <= min(open,close) <= max(open,close) <= high
        if not (self.low <= lo_body <= hi_body <= self.high):
            raise OHLCInvalidError(
                "OHLC 不変条件違反: low <= min(open,close) <= max(open,close) <= high",
                context={
                    "open": self.open,
                    "high": self.high,
                    "low": self.low,
                    "close": self.close,
                },
            )
        if self.spread < 0:
            raise OHLCInvalidError(
                "spread は非負である必要があります",
                context={"spread": self.spread},
            )
