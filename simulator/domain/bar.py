"""E-Bar: 価格バー（Value Object・CLEAN_ARCH §4）。

不変条件（DESIGN §3）:
    low <= min(open, close) <= max(open, close) <= high
    spread >= 0
違反時は :class:`OHLCInvalidError` を送出する。振る舞いなし（不変データ）。

時刻型は `simulator.domain.bar_time.EPOCH_CONVERTERS` が受理する表現に限る
（epoch int / ``numpy.int64`` / ``numpy.datetime64`` / ``datetime``）。構築時に
`is_supported_time` で表明し、違反は :class:`ConfigError` を送出する（ISSUE-411）。
表外の表現（ISO 文字列・float 等）の `Bar` を**そもそも作らせない**ことで、
下流が手書きの型分岐で推測解釈する原因を除去する（ISSUE-403 / ISSUE-412 の同型欠陥）。

依存規律: 本モジュールは numpy / pandas を import しない（`bar_time` と同じ。受理判定は
duck typing）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simulator.domain.bar_time import is_supported_time
from simulator.domain.exceptions import ConfigError, OHLCInvalidError


@dataclass(frozen=True)
class Bar:
    """1 本の価格バー。生成時に OHLC 整合と spread 非負を検証する。"""

    time: Any  # 受理表現は bar_time.EPOCH_CONVERTERS が唯一定義する（module docstring 参照）
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: int

    def __post_init__(self) -> None:
        # time の型契約を最初に表明する（OHLC 検査より先。未対応表現の Bar を作らせない）。
        if not is_supported_time(self.time):
            raise ConfigError(
                f"Bar.time が未対応の時刻表現です: {type(self.time).__name__}。"
                "time は epoch 秒 int（numpy.int64 可）または numpy.datetime64 を渡してください"
                "（datetime / pandas.Timestamp / ISO 文字列は Bar.time の契約外です）",
                context={
                    "value_type": type(self.time).__name__,
                    "value": str(self.time),
                },
            )
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
