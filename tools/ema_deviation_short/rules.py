"""EMA21_Dev8_Short.mq5 の売買規則（日足・足単位）。

規則（EA と同一）:
    水準   L[t] = round(EMA(close, period)[t-1] × (1 + deviation), digits) … 確定足の EMA を使う
    建て   保有なしの足 t で、open[t] >= L[t] なら open[t] で成行売り（指値を置けない位置）。
           それ以外で high[t] >= L[t] なら L[t] で指値売りが約定する。
    決済   建てた足から hold_bars 本後の足の始値で買い戻す（ask = open + spread × point）。
           損切り・利確は置かない。決済した足でも、保有が無くなれば同じ足で再び建てうる。
    除外   t < warmup の足は判定しない（EMA の初期値の影響が残るため）。
           決済足がデータ外の建玉は未決済として別に返す。

観測の境界（計算量検定の注入点）: `BarSource`。規則は列を 1 回ずつ読むだけで、
足ごとに読み直さない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


class BarSource(Protocol):
    """日足の列を渡す注入点。各メソッドは全バー分の配列を返す。"""

    def __len__(self) -> int: ...
    def opens(self) -> np.ndarray: ...
    def highs(self) -> np.ndarray: ...
    def closes(self) -> np.ndarray: ...
    def spreads(self) -> np.ndarray: ...


@dataclass(frozen=True)
class Trade:
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float

    @property
    def points(self) -> float:
        """損益（指数ポイント）。売りなので 建値 − 決済値。"""
        return self.entry_price - self.exit_price

    @property
    def return_pct(self) -> float:
        return self.points / self.entry_price * 100.0


@dataclass(frozen=True)
class Result:
    trades: "list[Trade]"
    open_entry_index: "int | None"  # 決済足がデータ外の建玉（無ければ None）


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """MT5 iMA(MODE_EMA) と同じ漸化式。初期値は最初の値。"""
    alpha = 2.0 / (period + 1.0)
    out = np.empty(len(values))
    prev = float(values[0])
    for i, v in enumerate(values):
        prev = prev + alpha * (float(v) - prev) if i else prev
        out[i] = prev
    return out


def simulate(
    source: BarSource,
    *,
    period: int,
    deviation: float,
    hold_bars: int,
    point: float,
    digits: int,
    warmup: int,
) -> Result:
    if hold_bars < 1:
        raise ValueError(f"hold_bars は 1 以上: {hold_bars}")
    n = len(source)
    opens = source.opens()
    highs = source.highs()
    spreads = source.spreads()
    levels = np.round(ema(source.closes(), period) * (1.0 + deviation), digits)

    trades: "list[Trade]" = []
    entry: "tuple[int, float] | None" = None
    for t in range(max(1, warmup), n):
        if entry is not None and t - entry[0] >= hold_bars:
            exit_price = float(opens[t]) + float(spreads[t]) * point
            trades.append(Trade(entry[0], t, entry[1], exit_price))
            entry = None
        if entry is not None:
            continue
        level = float(levels[t - 1])
        if opens[t] >= level:
            entry = (t, float(opens[t]))
        elif highs[t] >= level:
            entry = (t, level)
    return Result(trades, None if entry is None else entry[0])
