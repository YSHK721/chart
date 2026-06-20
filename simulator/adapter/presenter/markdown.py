"""MarkdownPresenter（ReportPresenterPort 実装）。

BacktestResult → DESIGN §8.2 標準 Markdown レポートへ変換する。jinja2 で
テンプレートをレンダリングする。present_json / present_html は本 Presenter の責務外
（各専用 Presenter が担う）。未対応操作の NotImplementedError は _BasePresenter が担う。

adapter 層は usecase + domain + 技術ドライバ（jinja2）のみに依存する。
"""
from __future__ import annotations

from typing import Any

from jinja2 import Template

from simulator.adapter.presenter._base import _BasePresenter

_MARKDOWN_TEMPLATE = """# Backtest Report: {{ ea_name }}

- Symbol: {{ symbol }}
- Period: {{ start }} ~ {{ end }}
- Initial Deposit: {{ s.initial_deposit }}

## Summary
| 指標 | 値 |
|---|---|
| Net Profit | {{ s.profit }} |
| Gross Profit / Loss | {{ s.gross_profit }} / {{ s.gross_loss }} |
| Profit Factor | {{ s.profit_factor }} |
| Recovery Factor | {{ s.recovery_factor }} |
| Sharpe Ratio | {{ s.sharpe_ratio }} |

## Drawdown
| 指標 | 値 |
|---|---|
| Balance DD | {{ s.balance_dd }} ({{ s.balance_dd_percent }}%) |
| Balance Min | {{ s.balance_min }} |

## Trade Statistics
| 指標 | 値 |
|---|---|
| Total Trades | {{ s.trades }} |
| Profit / Loss Trades | {{ s.profit_trades }} / {{ s.loss_trades }} |
| Long / Short Trades | {{ s.long_trades }} / {{ s.short_trades }} |
| Expected Payoff | {{ s.expected_payoff }} |
"""


class MarkdownPresenter(_BasePresenter):
    """BacktestResult を MT5 風 Markdown レポート文字列へ変換する。"""

    def present_markdown(self, result: Any) -> str:
        return Template(_MARKDOWN_TEMPLATE).render(
            ea_name=getattr(result, "ea_name", "Backtest"),
            symbol=getattr(result, "symbol", "-"),
            start=getattr(result, "start", "-"),
            end=getattr(result, "end", "-"),
            s=result.stats,
        )
