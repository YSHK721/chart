"""サマリー指標の計算ステップのログ（metrics_steps）の検定（2026-09-28 依頼者指示）。

状態検証: run の統計（compute_stats）と同じ入力から組むと全項目「一致」になる。統計の値を
    1 つ書き換えると、その項目だけが「不一致」になる（照合が実際に比べている＝恒真でない）。
計算量: 入力の読み取り回数 ÷ 入力の長さ が、長さ 40 と 4000 で同じ（＝入力に比例する回数
    しか読まない・二重ループが無い）。観測点は公開の入力（トレード列の pnl 読み取り）。
"""
from __future__ import annotations

import dataclasses

import pytest

from simulator.domain.trade_record import TradeRecord
from simulator.usecase.compute_stats import compute_stats
from simulator.usecase.metrics_steps import build_metrics_steps, render_metrics_steps

_PATTERN = (120.0, -40.0, 0.0, 55.0, -80.0, -10.0, 200.0, -35.0)


class _CountingTrade(TradeRecord):
    reads = 0

    def pnl(self):  # noqa: D401 — 読み取り回数の観測点（公開の入力）
        type(self).reads += 1
        return super().pnl()


def _trade(pnl, i, side):
    sign = 1 if side == "buy" else -1
    return _CountingTrade(
        side=side, volume=1.0, entry_time=i, exit_time=i + 1, entry_price=1000.0,
        exit_price=1000.0 + pnl / sign, contract_size=1.0, swap=0.0, commission=0.0,
        exit_reason="tp" if pnl > 0 else "sl",
    )


def _run(n):
    trades = [_trade(_PATTERN[i % len(_PATTERN)], i, "buy" if i % 3 else "sell") for i in range(n)]
    balance, b = [], 10000.0
    for t in trades:
        b += t.pnl()
        balance.append(b)
    equity = [x - 5.0 for x in balance]
    bar_equity = [10000.0] + balance
    stats = compute_stats(trades=trades, balance_curve=balance, equity_curve=equity,
                          initial_deposit=10000.0, bar_open_equity=bar_equity, bar_seconds=60.0)
    return dict(trades=trades, balance_curve=balance, equity_curve=equity, initial_deposit=10000.0,
                bar_open_equity=bar_equity, bar_seconds=60.0), stats


def test_every_metric_matches_the_run_statistics():
    inputs, stats = _run(40)
    text = render_metrics_steps(build_metrics_steps(stats=stats, **inputs))
    assert "不一致 0" in text
    assert "[不一致]" not in text
    # 全節（入力・損益・件数・個別・連勝連敗・DD・HPR・Sharpe・Z・LR）がある。
    for head in ("## 0.", "## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8.", "## 9."):
        assert head in text


def test_a_tampered_statistic_is_reported_as_a_mismatch():
    inputs, stats = _run(40)
    tampered = dataclasses.replace(stats, sharpe_ratio=stats.sharpe_ratio + 1.0)
    text = render_metrics_steps(build_metrics_steps(stats=tampered, **inputs))
    assert "[不一致] Sharpe Ratio" in text
    assert "不一致 1" in text


@pytest.mark.parametrize("n", [40, 4000])
def test_reads_grow_only_in_proportion_to_the_input(n):
    inputs, stats = _run(n)
    _CountingTrade.reads = 0
    build_metrics_steps(stats=stats, **inputs)
    per_trade = _CountingTrade.reads / n
    # 長さを 100 倍にしても 1 トレードあたりの読み取り回数は同じ（比例のみ・固定値は焼き込まない）。
    _CountingTrade.reads = 0
    small_inputs, small_stats = _run(40)
    _CountingTrade.reads = 0
    build_metrics_steps(stats=small_stats, **small_inputs)
    assert per_trade == pytest.approx(_CountingTrade.reads / 40, rel=0.05)
