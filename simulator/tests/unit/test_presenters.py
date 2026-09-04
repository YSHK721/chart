"""MarkdownPresenter / JsonPresenter（ReportPresenterPort 実装）テスト（cycle B / B4）。

BacktestResult → DESIGN §8.2 表現へ変換する。Presenter モックでなく実出力を検証する
（markdown: 必須要素 / json: stats 値再 load 一致）。
"""
from __future__ import annotations

import abc
import json

import pandas as pd

from simulator.usecase.models import BacktestResult, BacktestStats
from simulator.usecase.ports import (
    JsonReportPort,
    MarkdownReportPort,
    ReportPresenterPort,
)


def _stats():
    return BacktestStats(
        initial_deposit=10000.0,
        profit=1500.0,
        gross_profit=2000.0,
        gross_loss=-500.0,
        profit_factor=4.0,
        recovery_factor=3.0,
        expected_payoff=15.0,
        sharpe_ratio=1.25,
        trades=100,
        profit_trades=60,
        loss_trades=40,
        long_trades=50,
        short_trades=50,
        profit_long_trades=30,
        profit_short_trades=30,
        balance_min=9500.0,
        balance_dd=500.0,
        balance_dd_percent=5.0,
        balance_dd_relative=500.0,
        balance_ddrel_percent=5.0,
        max_profit_trade=300.0,
        max_loss_trade=-200.0,
        max_con_wins=5,
        max_con_profit_trades=900.0,
        max_con_losses=3,
        max_con_loss_trades=-400.0,
        con_profit_max=900.0,
        con_profit_max_trades=5,
        con_loss_max=-400.0,
        con_loss_max_trades=3,
        profit_trades_avg_con=2.0,
        loss_trades_avg_con=1.5,
    )


def _result():
    trades = pd.DataFrame(
        {
            "side": ["buy", "sell"],
            "entry_price": [1.10, 1.20],
            "exit_price": [1.15, 1.18],
            "profit": [50.0, -20.0],
        }
    )
    return BacktestResult(
        trades=trades,
        deals=[],
        equity_curve=[10000.0, 10050.0, 10030.0],
        balance_curve=[10050.0, 10030.0],
        stats=_stats(),
    )


# ---- MarkdownPresenter ----

def test_markdown_presenter_is_report_presenter_port_subclass():
    from simulator.adapter.presenter.markdown import MarkdownPresenter

    assert issubclass(MarkdownPresenter, MarkdownReportPort)
    assert issubclass(ReportPresenterPort, abc.ABC)
    assert isinstance(MarkdownPresenter(), MarkdownReportPort)


def test_markdown_contains_summary_required_elements():
    from simulator.adapter.presenter.markdown import MarkdownPresenter

    md = MarkdownPresenter().present_markdown(_result())

    assert isinstance(md, str)
    assert "Backtest Report" in md
    assert "Summary" in md
    assert "Net Profit" in md
    assert "1500.0" in md  # profit 値
    assert "Profit Factor" in md
    assert "4.0" in md  # profit_factor 値


# ---- JsonPresenter ----

def test_json_presenter_is_report_presenter_port_subclass():
    from simulator.adapter.presenter.json import JsonPresenter

    assert issubclass(JsonPresenter, JsonReportPort)
    assert isinstance(JsonPresenter(), JsonReportPort)


def test_json_present_writes_stats_values_reloadable(tmp_path):
    from simulator.adapter.presenter.json import JsonPresenter

    p = tmp_path / "report.json"
    JsonPresenter().present_json(_result(), p)

    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["stats"]["profit"] == 1500.0
    assert loaded["stats"]["trades"] == 100
    assert loaded["stats"]["profit_factor"] == 4.0
