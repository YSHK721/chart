"""値オブジェクト: Bar / SeriesPoint / RunningExtreme（基本設計書 §6・§5.5.4）。

`RunningExtreme` は「走行 H / L」＝形成中バーの高値・安値であり、次の 2 つに使う。

1. §5.5.4 の前進評価: 終値候補 `C` を置いたときの足は `H = max(H0, C)` / `L = min(L0, C)`
   になる（参照実装 `tools/measure/issue449/probe_heatmap.py:136-139` と同一規約）。
2. UC-02 の epoch `(bar_time, run_hi, run_lo)`: これが不変ならメビウス係数は不変であり、
   前進評価を 1 回も発行してはならない（§7 の計算量表明）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _require_finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} は有限値が必要です: {value!r}")
    return number


@dataclass(frozen=True)
class Bar:
    """確定足または形成中足 1 本。`time` は UNIX 秒（marketdata の date 列は UTC）。"""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close", "volume"):
            _require_finite(name, getattr(self, name))
        if self.high < self.low:
            raise ValueError(f"high < low の足は不正です: high={self.high}, low={self.low}")
        if self.volume < 0.0:
            raise ValueError(f"volume は非負が必要です: {self.volume}")


@dataclass(frozen=True)
class SeriesPoint:
    """系列 1 点。`value` は NaN を許す（warm-up ＝「水準なし」を無言で落とさない・§5.2）。"""

    time: int
    value: float


@dataclass(frozen=True)
class RunningExtreme:
    """形成中バーの走行極値（走行 H / L）。"""

    high: float
    low: float

    def __post_init__(self) -> None:
        _require_finite("high", self.high)
        _require_finite("low", self.low)
        if self.high < self.low:
            raise ValueError(f"high < low は不正です: high={self.high}, low={self.low}")

    @classmethod
    def of(cls, bar: Bar) -> "RunningExtreme":
        return cls(high=bar.high, low=bar.low)

    def extended_by(self, close: float) -> "RunningExtreme":
        """終値候補 `close` を置いたときの走行極値（参照実装と同一の max/min 規約）。"""
        price = _require_finite("close", close)
        return RunningExtreme(high=max(self.high, price), low=min(self.low, price))
