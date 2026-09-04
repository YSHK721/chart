"""MTF 投影で増分器の ``prepare`` が時点数に比例しないことを固定する（ISSUE-450 第 5 段）。

固定するのは出力の正しさではなく **無駄の不在**。ここで数えるのは ``prepare`` の呼び出し回数で、
出力が正しいままいくらでも増えうる量である。

実測（是正前・2026-08-28・ライブサーバ再起動直後）:
    冷えた状態の MTF 1 本が 311〜428 ms。表示時間足 4h には MTF が 6 本あり合計 2,270 ms＝
    起動全体の 77% を占めた。プロファイルでは ``moving_averages.prepare`` が 1 リクエストで
    **500 回**（＝出力窓の時点数ぶん）走り、そのたびに確定プレフィクス全体の numpy 配列を
    作り直していた（`adapt` の `np.array_equal` も同じく O(プレフィクス長)）。

規約上、1 つの期間の中で変わるのは**畳んだ末尾 1 本だけ**であり、確定プレフィクスは不変である。
したがって ``prepare`` は**期間ごとに 1 回**で足りる。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from adapter.compute import incremental as incremental_registry
from adapter.compute.indicator_compute_adapter import IndicatorComputeAdapter
from adapter.compute.latest_dispatch import latest_compute, latest_seq_compute
from adapter.compute.mtf_causal_frames import causal_mtf_frames

HOUR = 3600
DAY = 86400

_MA = {"ma_type": "ema", "length": 5, "source": "hlc3", "offset": 0,
       "smoothing_type": "none", "smoothing_length": 9, "bb_stddev": 2}


def _label(tf: str, unix_sec: int) -> int:
    """1D セッション足と同型のラベル（期間の右端の深夜）。"""
    return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY


def _frame(times) -> "pd.DataFrame":
    n = len(times)
    return pd.DataFrame(
        {"open": [100.0 + i * 0.1 for i in range(n)],
         "high": [101.0 + i * 0.1 for i in range(n)],
         "low": [99.0 + i * 0.1 for i in range(n)],
         "close": [100.5 + i * 0.1 for i in range(n)],
         "volume": [1.0] * n},
        index=pd.to_datetime(list(times), unit="s"))


#: H 足の履歴日数。MA(length=5) が値を出せる長さを確保する（不足すると系列が空になり、
#:   「一致した」ではなく「両方とも空」を比べる無意味なテストになる）。
_SOURCE_DAYS = 60
#: C 足を置く期間（H 足履歴の末尾側）。confirmed プレフィクスが十分に長くなる位置。
_CHART_START_DAY = 250


def _chart(n_days: int, per_day: int) -> "pd.DataFrame":
    times = []
    for d in range(n_days):
        base = (_CHART_START_DAY + d) * DAY - 3 * HOUR
        step = (3 * HOUR) // max(per_day, 1)
        times.extend(base + i * step for i in range(per_day))
    return _frame(times)


def _source(_unused: int = 0) -> "pd.DataFrame":
    """C 足の期間より前から連なる H 足（確定プレフィクスを十分な長さにする）。"""
    first = _CHART_START_DAY - _SOURCE_DAYS
    return _frame([(first + i) * DAY for i in range(_SOURCE_DAYS + 4)])


class _PrepareCounter:
    """``prepare`` の呼び出し回数を数える（Test Spy）。"""

    def __init__(self, monkeypatch) -> None:
        monkeypatch.undo()
        self.calls = 0
        incrementer = incremental_registry.resolve("moving_averages")
        real = type(incrementer).prepare

        def counting(inner_self, df, params):
            self.calls += 1
            return real(inner_self, df, params)

        monkeypatch.setattr(type(incrementer), "prepare", counting)


def _run(chart, source, *, limit):
    adapter = IndicatorComputeAdapter()
    call = lambda df: latest_compute(adapter, "moving_averages", "default", df, dict(_MA))
    call_seq = lambda df, bars: latest_seq_compute(
        adapter, "moving_averages", "default", df, bars, dict(_MA))
    return causal_mtf_frames(
        df_chart=chart.tail(limit), df_source=source, compute_tf="1D",
        bar_time_unix=_label, compute_latest=call, compute_latest_seq=call_seq,
        fold_from=chart, memo=None)


def test_prepare_does_not_grow_with_the_points_inside_a_period(monkeypatch) -> None:
    """1 期間の時点数を増やしても ``prepare`` の回数は増えない。

    期間内で変わるのは畳んだ末尾 1 本だけで、確定プレフィクスは不変だからである。
    """
    few_chart, source = _chart(n_days=3, per_day=4), _source(4)
    many_chart = _chart(n_days=3, per_day=48)

    counter_few = _PrepareCounter(monkeypatch)
    _run(few_chart, source, limit=8)
    few = counter_few.calls

    counter_many = _PrepareCounter(monkeypatch)
    _run(many_chart, source, limit=96)
    many = counter_many.calls

    assert many <= few, (
        f"1 期間の時点数を 12 倍にしたら prepare が {few} → {many} 回に増えた。"
        "時点ごとに確定プレフィクスを作り直している")


def test_projected_values_are_unchanged_by_the_sequence_path(monkeypatch) -> None:
    """逐次経路（prepare 1 回）と従来経路（時点ごと prepare）の値が一致する。

    速さのために値が変わっては意味がない。ここが本命の不変条件である。
    """
    chart, source = _chart(n_days=3, per_day=12), _source(4)
    adapter = IndicatorComputeAdapter()
    call = lambda df: latest_compute(adapter, "moving_averages", "default", df, dict(_MA))
    common = dict(df_chart=chart.tail(24), df_source=source, compute_tf="1D",
                  bar_time_unix=_label, fold_from=chart, memo=None)

    without = causal_mtf_frames(compute_latest=call, **common)
    with_seq = causal_mtf_frames(
        compute_latest=call,
        compute_latest_seq=lambda df, bars: latest_seq_compute(
            adapter, "moving_averages", "default", df, bars, dict(_MA)),
        **common)

    def points(series):
        return [(s.get("name"), [(p["time"], p["value"]) for p in s.get("data") or []])
                for s in series or []]

    assert points(with_seq) == points(without), "逐次経路で値が変わった"
    assert points(without), "そもそも値が出ていない（テストが空を比較している）"
