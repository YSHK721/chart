"""21EMA 上方乖離ショート規則の状態検証（建て・決済・除外の境界）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tools.ema_deviation_short.rules import ema, simulate
from tools.ema_deviation_short.source import FrameBarSource


def _source(opens, highs, closes, spread=0.0):
    n = len(opens)
    return FrameBarSource(pd.DataFrame({
        "date": [f"d{i}" for i in range(n)], "open": opens, "high": highs,
        "close": closes, "spread": [spread] * n,
    }))


def _run(source, hold=2, warmup=1, deviation=0.08):
    return simulate(source, period=21, deviation=deviation, hold_bars=hold, point=0.1, digits=1, warmup=warmup)


def test_ema_matches_mt5_recurrence():
    values = np.array([100.0, 110.0, 90.0])
    a = 2.0 / 22.0
    e1 = 100.0 + a * 10.0
    assert ema(values, 21) == pytest.approx([100.0, e1, e1 + a * (90.0 - e1)])


def test_limit_fills_at_level_when_high_touches():
    # EMA は全足 100 → 水準 108.0。足 2 の高値が 108 に触れる。
    r = _run(_source([100, 100, 100, 100, 100], [100, 100, 109, 100, 100], [100] * 5))
    assert [(t.entry_index, t.entry_price, t.exit_index, t.exit_price) for t in r.trades] == [(2, 108.0, 4, 100.0)]


def test_gap_above_level_sells_at_open():
    r = _run(_source([100, 100, 110, 100, 100], [100, 100, 111, 100, 100], [100] * 5))
    assert r.trades[0].entry_price == 110.0


def test_no_entry_below_level():
    r = _run(_source([100] * 5, [107.9] * 5, [100] * 5))
    assert r.trades == [] and r.open_entry_index is None


def test_exit_pays_spread_and_unclosed_is_reported():
    r = _run(_source([100] * 4, [100, 109, 100, 100], [100] * 4, spread=50), hold=2)
    assert r.trades[0].exit_price == pytest.approx(105.0)  # 100 + 50 × 0.1
    r2 = _run(_source([100] * 4, [100, 100, 109, 100], [100] * 4), hold=2)
    assert r2.trades == [] and r2.open_entry_index == 2


def test_warmup_bars_are_not_judged():
    r = _run(_source([100] * 5, [100, 109, 100, 100, 100], [100] * 5), warmup=2)
    assert r.trades == []


def test_reentry_on_exit_bar():
    r = _run(_source([100] * 6, [100, 109, 100, 109, 100, 100], [100] * 6), hold=2)
    assert [(t.entry_index, t.exit_index) for t in r.trades] == [(1, 3), (3, 5)]


def test_hold_bars_must_be_positive():
    with pytest.raises(ValueError):
        _run(_source([100] * 3, [100] * 3, [100] * 3), hold=0)
