"""窓つき読み手は足の時刻を 1 回の一括変換で epoch 秒にする（ISSUE-553・計算量）。

なぜ在るか（実測 2026-09-29）: 「`WindowedMarketDataRepository`」 の 「`load`」 は内側の足を窓で絞るため、
内側の足全部の時刻を epoch 秒へ変換する。是正前は足ごとに 「`epoch_seconds`」 を呼んでいた
（1 run に 1 回・MT5 タブ形式の照合データ 28,097 本で 28,097 回＋窓の端 2 回。窓を 3 日に狭めても同じ回数）。

表明（観測は宣言された注入点 「`set_observer`」（`simulator/usecase/bar_times.py`）だけ）:
    1. 変換した時刻の数 − 窓を決めるのに使った時刻の数（内側の足の本数）= 0。
    2. 変換の呼び出しは load 1 回につき 1 回で、窓の幅を変えた 2 点で同じ（足ごとに増えない）。
    3. datetime64 の足は一括の経路を通る。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest

from simulator.adapter.repository.windowed_market_data import WindowedMarketDataRepository
from simulator.domain.bar import Bar
from simulator.usecase import bar_times
from simulator.usecase.ports import MarketDataPort

_U = timezone.utc


class _Port(MarketDataPort):
    def __init__(self, bars: "list[Bar]") -> None:
        self._bars = bars

    def load(self, source_ref: Any, timeframe: Any = None, period: Any = None) -> "list[Bar]":
        return list(self._bars)


def _bars(days: int) -> "list[Bar]":
    """1 時間足で ``days`` 日ぶん（MT5 タブ形式ローダの実読型 datetime64）。"""
    start = np.datetime64("2024-01-01T00:00:00")
    return [
        Bar(time=start + np.timedelta64(h, "h"), open=1.0, high=1.0, low=1.0, close=1.0,
            volume=1.0, spread=0)
        for h in range(24 * days)
    ]


@pytest.fixture
def observed():
    seen: "list[tuple[int, bool]]" = []
    bar_times.set_observer(lambda count, batched: seen.append((count, batched)))
    yield seen
    bar_times.set_observer(None)


def _load(window, bars, observed):
    observed.clear()
    kept = WindowedMarketDataRepository(_Port(bars), window=window).load("ref")
    return kept, list(observed)


@pytest.mark.parametrize("last_day", [3, 9])
def test_converted_times_equal_the_times_used_to_decide_the_window(observed, last_day) -> None:
    bars = _bars(10)
    window = (datetime(2024, 1, 2, tzinfo=_U), datetime(2024, 1, last_day, tzinfo=_U))

    kept, seen = _load(window, bars, observed)

    assert kept, "窓に足が残らないと比較が空振りする"
    assert sum(count for count, _ in seen) - len(bars) == 0
    assert all(batched for _, batched in seen)


def test_a_wider_window_does_not_issue_more_conversion_calls(observed) -> None:
    bars = _bars(10)
    _, narrow = _load((datetime(2024, 1, 2, tzinfo=_U), datetime(2024, 1, 3, tzinfo=_U)), bars, observed)
    _, wide = _load((datetime(2024, 1, 2, tzinfo=_U), datetime(2024, 1, 9, tzinfo=_U)), bars, observed)

    assert narrow, "変換が観測口を通っていない（足ごとの変換へ戻った）"
    assert len(wide) - len(narrow) == 0
