"""TDD: build_degradation_report / extract_metrics（詳細設計 §6.2.2 / C-2）。

全主要指標について ratio(OOS/IS)・delta(OOS-IS) を両格納し、IS=0 のとき ratio=None。
"""
from __future__ import annotations

from typing import Any

from simulator.usecase.run_is_oos import (
    DegradationReport,
    MetricDegradation,
    RunIsOosRequest,
    build_degradation_report,
    extract_metrics,
)


class _StatsStub:
    """劣化対象 6 フィールドのみ意味を持つ最小 stats stub（getattr で抽出される）。"""

    def __init__(self, **kw: Any) -> None:
        # 既定 6 指標を 0.0 で初期化し、与えられた値で上書き
        for n in (
            "profit",
            "profit_factor",
            "recovery_factor",
            "expected_payoff",
            "sharpe_ratio",
            "trades",
        ):
            setattr(self, n, 0.0)
        for k, v in kw.items():
            setattr(self, k, v)


_DEFAULT_NAMES = RunIsOosRequest(split=0, is_trading_start=0).metric_names


def test_extract_metrics_returns_float_values_by_name():
    # Arrange
    stats = _StatsStub(profit=100, trades=5)
    # Act
    m = extract_metrics(stats, ("profit", "trades"))
    # Assert
    assert m == {"profit": 100.0, "trades": 5.0}
    assert all(isinstance(v, float) for v in m.values())


def test_build_degradation_report_computes_ratio_and_delta():
    # Arrange
    is_stats = _StatsStub(profit=100)
    oos_stats = _StatsStub(profit=50)
    # Act
    report = build_degradation_report(is_stats, oos_stats, ("profit",))
    # Assert
    m = report.by_name("profit")
    assert isinstance(m, MetricDegradation)
    assert m.is_value == 100.0
    assert m.oos_value == 50.0
    assert m.ratio == 0.5
    assert m.delta == -50.0


def test_build_degradation_report_ratio_is_none_on_zero_is_but_delta_kept():
    # Arrange: IS=0 でゼロ除算
    is_stats = _StatsStub(profit=0)
    oos_stats = _StatsStub(profit=50)
    # Act
    report = build_degradation_report(is_stats, oos_stats, ("profit",))
    # Assert: ratio=None（未定義）だが delta は常に格納
    m = report.by_name("profit")
    assert m.ratio is None
    assert m.delta == 50.0


def test_build_degradation_report_covers_all_six_default_metrics_in_order():
    # Arrange
    is_stats = _StatsStub(
        profit=1, profit_factor=2, recovery_factor=3,
        expected_payoff=4, sharpe_ratio=5, trades=6,
    )
    oos_stats = _StatsStub(
        profit=1, profit_factor=2, recovery_factor=3,
        expected_payoff=4, sharpe_ratio=5, trades=6,
    )
    # Act
    report = build_degradation_report(is_stats, oos_stats, _DEFAULT_NAMES)
    # Assert: 6 件・name 順一致
    assert isinstance(report, DegradationReport)
    assert [m.name for m in report.metrics] == list(_DEFAULT_NAMES)
    assert len(report.metrics) == 6


def test_build_degradation_report_trades_degradation_values():
    # Arrange: 先例値 trades 5224 -> 2438
    is_stats = _StatsStub(trades=5224)
    oos_stats = _StatsStub(trades=2438)
    # Act
    report = build_degradation_report(is_stats, oos_stats, ("trades",))
    # Assert
    m = report.by_name("trades")
    assert m.delta == -2786.0
    assert m.ratio is not None
    assert abs(m.ratio - (2438.0 / 5224.0)) < 1e-12


def test_by_name_returns_none_for_unknown_metric():
    # Arrange
    report = build_degradation_report(_StatsStub(), _StatsStub(), ("profit",))
    # Act / Assert
    assert report.by_name("nonexistent") is None
