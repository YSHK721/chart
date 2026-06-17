"""MarkdownPresenter / JsonPresenter / HtmlPresenter（ReportPresenterPort 実装）テスト（cycle B / B4）。

BacktestResult → DESIGN §8.2 表現へ変換する。Presenter モックでなく実出力を検証する
（markdown: 必須要素 / json: stats 値再 load 一致 / html: チャート埋込タグ）。
"""
from __future__ import annotations

import abc
import json
import re

import pandas as pd
import pytest

from backtest.usecase.models import BacktestResult, BacktestStats
from backtest.usecase.ports import ReportPresenterPort


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
    from backtest.adapter.presenter.markdown import MarkdownPresenter

    assert issubclass(MarkdownPresenter, ReportPresenterPort)
    assert issubclass(ReportPresenterPort, abc.ABC)
    assert isinstance(MarkdownPresenter(), ReportPresenterPort)


def test_markdown_contains_summary_required_elements():
    from backtest.adapter.presenter.markdown import MarkdownPresenter

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
    from backtest.adapter.presenter.json import JsonPresenter

    assert issubclass(JsonPresenter, ReportPresenterPort)
    assert isinstance(JsonPresenter(), ReportPresenterPort)


def test_json_present_writes_stats_values_reloadable(tmp_path):
    from backtest.adapter.presenter.json import JsonPresenter

    p = tmp_path / "report.json"
    JsonPresenter().present_json(_result(), p)

    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["stats"]["profit"] == 1500.0
    assert loaded["stats"]["trades"] == 100
    assert loaded["stats"]["profit_factor"] == 4.0


# ---- HtmlPresenter ----

def test_html_presenter_is_report_presenter_port_subclass():
    from backtest.adapter.presenter.html import HtmlPresenter

    assert issubclass(HtmlPresenter, ReportPresenterPort)
    assert isinstance(HtmlPresenter(), ReportPresenterPort)


def test_html_present_writes_chart_embed_and_stats(tmp_path):
    from backtest.adapter.presenter.html import HtmlPresenter

    p = tmp_path / "report.html"
    HtmlPresenter().present_html(_result(), p)

    html = p.read_text(encoding="utf-8")
    assert "<html" in html.lower()
    # lightweight-charts 埋込（チャートライブラリ参照タグ）
    assert "lightweight-charts" in html
    # stats 値が埋め込まれている
    assert "1500" in html


def _result_with_ea_name(ea_name):
    # html.py は ea_name を getattr で参照する。BacktestResult は ea_name を持たない
    # ため、必要属性を備えた最小スタンドインを使う（usecase/models は不可触）。
    import types

    r = _result()
    return types.SimpleNamespace(
        ea_name=ea_name,
        symbol="USDJPY",
        stats=r.stats,
        equity_curve=r.equity_curve,
    )


# ---- 🟡-1 XSS: autoescape ----

def test_html_escapes_script_in_ea_name(tmp_path):
    # 🟡-1: ユーザ由来文字列（ea_name）に <script> が含まれても、生のスクリプトとして
    # 出力されてはならない（autoescape=True で HTML エンティティ化されること）。
    from backtest.adapter.presenter.html import HtmlPresenter

    p = tmp_path / "report.html"
    HtmlPresenter().present_html(
        _result_with_ea_name("<script>alert(1)</script>"), p
    )

    html = p.read_text(encoding="utf-8")
    # 生の <script>alert(1)</script>（ea_name 由来）が混入していない
    assert "<script>alert(1)</script>" not in html
    # エスケープ済みエンティティとして出力されている
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# ---- 🟡-2 equity_points を妥当な JSON 配列で埋め込む ----

def test_html_equity_points_is_valid_json_array(tmp_path):
    # 🟡-2: equity データは Python dict repr ではなく json.dumps による妥当な JSON
    # 配列で埋め込まれること（テンプレ {{ equity_points | safe }}）。
    from backtest.adapter.presenter.html import HtmlPresenter

    p = tmp_path / "report.html"
    HtmlPresenter().present_html(_result(), p)

    html = p.read_text(encoding="utf-8")
    # setData([...]) に渡される配列を抽出して JSON として parse できることを確認
    m = re.search(r"series\.setData\((\[.*?\])\)", html, re.DOTALL)
    assert m is not None, "setData(...) の配列が見つからない"
    points = json.loads(m.group(1))  # Python dict repr ('time': ...) なら ValueError
    assert isinstance(points, list)
    assert points[0] == {"time": 0, "value": 10000.0}
