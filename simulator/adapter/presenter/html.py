"""HtmlPresenter（ReportPresenterPort 実装）。

BacktestResult → HTML レポート（jinja2 テンプレ + lightweight-charts 埋込）へ変換する。
DESIGN §8.2 の Summary 表現を HTML 化し、equity/balance カーブを lightweight-charts で
描画するためのスクリプトタグを埋め込む。

adapter 層は usecase + domain + 技術ドライバ（jinja2 / lightweight-charts）のみに依存する。
"""
from __future__ import annotations

import json
from typing import Any

from jinja2 import Environment

from simulator.adapter.presenter._base import _BasePresenter

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Backtest Report: {{ ea_name }}</title>
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
<h1>Backtest Report: {{ ea_name }}</h1>
<ul>
  <li>Symbol: {{ symbol }}</li>
  <li>Initial Deposit: {{ s.initial_deposit }}</li>
</ul>
<h2>Summary</h2>
<table>
  <tr><th>Net Profit</th><td>{{ s.profit }}</td></tr>
  <tr><th>Profit Factor</th><td>{{ s.profit_factor }}</td></tr>
  <tr><th>Recovery Factor</th><td>{{ s.recovery_factor }}</td></tr>
  <tr><th>Sharpe Ratio</th><td>{{ s.sharpe_ratio }}</td></tr>
  <tr><th>Total Trades</th><td>{{ s.trades }}</td></tr>
</table>
<div id="equity-chart"></div>
<script>
  // lightweight-charts による equity カーブ描画
  const chart = LightweightCharts.createChart(document.getElementById('equity-chart'));
  const series = chart.addLineSeries();
  series.setData({{ equity_points | safe }});
</script>
</body>
</html>
"""


class HtmlPresenter(_BasePresenter):
    """BacktestResult を lightweight-charts 埋込 HTML レポートへ変換する。"""

    def present_html(self, result: Any, path: Any) -> None:
        equity = list(result.equity_curve or [])
        equity_points = [{"time": i, "value": float(v)} for i, v in enumerate(equity)]
        # autoescape=True で ea_name/symbol 等のユーザ由来文字列を HTML エスケープ
        # （XSS 防止）。チャート用 equity データのみ JSON 化して | safe で限定挿入する。
        env = Environment(autoescape=True)
        html = env.from_string(_HTML_TEMPLATE).render(
            ea_name=getattr(result, "ea_name", "Backtest"),
            symbol=getattr(result, "symbol", "-"),
            s=result.stats,
            equity_points=json.dumps(equity_points),
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
