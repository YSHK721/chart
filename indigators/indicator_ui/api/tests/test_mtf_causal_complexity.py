"""上位足(MTF)因果系列の**計算量テスト**（ISSUE-450・CLAUDE.md 絶対命令 2026-08-28）。

固定するのは出力の正しさではなく **無駄の不在** である。

    発行した計算 − 出力に使った計算 = 0

「作ってから捨てる」欠陥は出力が正しいままなので、状態検証（出力を見る通常のテスト）では
原理的に落ちない。実際 ISSUE-450 では既存テスト 1,233 件が緑のまま 20 日間この浪費を保護し、
1m チャートで 1 ティックあたり 12.2 秒・破棄率 98.0% を生んでいた。

回数そのもの（「N 回呼ばれること」）は固定しない。それをやると浪費が仕様へ昇格する
（旧 `test_window_bars_limits_the_output_but_not_the_folding` が実際にそうなった）。
ここで固定するのは次の 2 点だけ:

  1. 発行した計算はすべて出力に使われる（捨てる発行が 0 件）。
  2. 発行数は**出力窓の本数**で決まり、畳みに要る窓外の本数では増えない（オーダーの表明）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from adapter.compute.mtf_causal import causal_mtf_series

HOUR = 3600
DAY = 86400


def _label(tf: str, unix_sec: int) -> int:
    """1D セッション足と同型のラベル（期間の右端の深夜）。fake だが実物と同じ形。"""
    return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY


def _chart(n_periods: int, per_period: int) -> "list[dict]":
    """``n_periods`` 期間 × ``per_period`` 本の C 足を作る（時刻昇順）。"""
    bars = []
    for p in range(n_periods):
        base = (9 + p) * DAY - 3 * HOUR
        step = (3 * HOUR) // max(per_period, 1)
        for i in range(per_period):
            t = base + i * step
            bars.append({"time": t, "open": 1.0 + i, "high": 2.0 + i,
                         "low": 0.5 + i, "close": 1.5 + i, "volume": 1.0})
    return bars


def _source(n: int) -> "list[dict]":
    """H 足（確定プレフィクス用）。C 足の期間ラベルと同じ座標に置く。"""
    return [{"time": (9 + i) * DAY, "open": 1.0, "high": 2.0,
             "low": 0.5, "close": 1.5, "volume": 10.0} for i in range(n)]


class _CountingSeq:
    """発行された計算を数える Test Spy。

    ``latest_seq(prefix_bars, tails)`` は tails 1 件につき 1 計算を発行する契約なので、
    tails の総数が「発行した計算」の件数になる。返す点の time は畳み足のラベルではなく
    **要求された時点**が使われる（呼び出し側が付け替える）ので、ここでは値だけ返せばよい。
    """

    def __init__(self) -> None:
        self.issued: "list[dict]" = []

    def __call__(self, prefix_bars, tails):
        self.issued.extend(t[-1] for t in tails)
        return [[{"name": "MA", "kind": "line",
                  "data": [{"time": t[-1]["time"], "value": t[-1]["close"]}]}] for t in tails]


def _run(chart_bars, source_bars, window_bars, compute_tf="1D"):
    spy = _CountingSeq()
    out = causal_mtf_series(
        chart_bars=chart_bars, source_bars=source_bars, compute_tf=compute_tf,
        bar_time_unix=_label, latest_seq=spy, window_bars=window_bars, memo=None)
    used = sum(len(p.get("data") or []) for p in out)
    return len(spy.issued), used


@pytest.mark.parametrize("head_len", [0, 1, 5, 40, 400])
def test_no_computation_is_issued_and_then_discarded(head_len: int) -> None:
    """窓外の C 足がいくら増えても、捨てる発行は 0 件のままである。

    ``head_len`` は「出力窓の前に連なる同一期間の C 足」の本数。畳みには要るが出力には
    使わない。ここが発行に載ると、本数ぶんそのまま捨てる計算になる（ISSUE-450 の真因 A）。
    """
    per_period = head_len + 3
    chart = _chart(n_periods=3, per_period=per_period)
    window = [b for b in chart if b["time"] >= chart[head_len]["time"]]

    issued, used = _run(chart, _source(4), window)

    assert issued == used, (
        f"発行 {issued} 件に対し出力に使ったのは {used} 件。"
        f"差 {issued - used} 件は作ってから捨てている（窓外 {head_len} 本）")


def test_issued_count_scales_with_the_output_window_not_the_folding_span() -> None:
    """発行数は出力窓の本数で決まり、畳みに要る窓外の本数では増えない（オーダーの表明）。

    同じ出力窓に対して窓外の C 足だけを 10 倍にしても、発行数は変わってはならない。
    """
    small = _chart(n_periods=2, per_period=12)
    large = _chart(n_periods=2, per_period=120)
    win_small = small[-4:]
    win_large = large[-4:]

    issued_small, _ = _run(small, _source(3), win_small)
    issued_large, _ = _run(large, _source(3), win_large)

    assert issued_small == issued_large == len(win_small), (
        f"窓外を 10 倍にしたら発行が {issued_small} → {issued_large} に増えた。"
        "発行数は出力窓の本数だけで決まらなければならない")


def test_no_waste_when_the_window_is_a_single_bar() -> None:
    """足内更新（mode=latest）の形。出力 1 本に対して発行も 1 件でなければならない。

    ここが ISSUE-450 で最も高くついた経路で、1m チャートの 1 ティックあたり 12.2 秒だった。
    """
    chart = _chart(n_periods=2, per_period=500)
    window = chart[-1:]

    issued, used = _run(chart, _source(3), window)

    assert (issued, used) == (1, 1), (
        f"末尾 1 本の更新に {issued} 件の計算を発行している（使ったのは {used} 件）")
