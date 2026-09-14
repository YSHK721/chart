"""UC-002: STAT_* を確定トレード列・balance/equity 系列から決定論的に算出する。

参照仕様の二重定義（ISSUE-094 🔵）を分離した結線層。統計式の実体は 2 つの参照仕様
モジュールへ分割済みで、本モジュールは「どちらの版を採るか」の結線（compute_stats）
のみを担う：

    * metrics_spec.py … 参照仕様① METRICS 文書版（HPR Sharpe・balance 基準 recovery・
      AHPR/GHPR・balance DD・連勝/連敗ラン・§1 損益サマリー）。
    * mt5_parity.py  … 参照仕様② MT5 校正版（クランプ Sharpe・equity 符号付き recovery・
      equity DD・件数規則 pnl>=0・MT5 Z-Score）。

旧 API 互換のため、両モジュールの公開名は本モジュールから再エクスポートする（既存の
`from simulator.usecase.compute_stats import <name>` を温存）。

実 MT5 校正（ISSUE-013 / golden: tests/fixtures/mt5/ma_slope_jp225_202501/expected/report.json）:
    本プロジェクト目的＝MT5 再現につき、BACKTEST_METRICS.md と実 MT5 が割れる点は実 MT5 を正とする。
    どの統計がどちらの参照仕様に属するかの詳細は各モジュール docstring を参照。

判断点（doc 不整合・upstream-input-validation で実証・据え置き）:
    * Sharpe / σ(HPR): METRICS §1.2/§11 の式（ddof=0 母分散）を採用（metrics_spec.sharpe_ratio）。
      §12.2/§12.6 記載の σ=0.020019・Sharpe=0.17 は式から再現不能のため不採用。
      実 MT5 の Sharpe=-5.0 は per-trade クランプ版（mt5_parity.sharpe_ratio_per_trade）を結線。
"""
from __future__ import annotations

from typing import Sequence

from simulator.domain.trade_record import TradeRecord
from simulator.usecase.models import BacktestStats

# --- 参照仕様① METRICS 文書版（再エクスポート）---
from simulator.usecase.metrics_spec import (  # noqa: F401
    ahpr,
    average_consecutive_losses,
    average_consecutive_wins,
    balance_dd_absolute,
    balance_dd_maximal,
    balance_dd_maximal_percent,
    balance_dd_relative_amount,
    balance_dd_relative_percent,
    balance_min,
    expected_payoff,
    ghpr,
    gross_loss,
    gross_profit,
    is_run_win,
    largest_loss_trade,
    largest_profit_trade,
    long_trades,
    max_consecutive_losses_count,
    max_consecutive_losses_loss,
    max_consecutive_wins_count,
    max_consecutive_wins_profit,
    maximal_consecutive_loss_amount,
    maximal_consecutive_loss_count,
    maximal_consecutive_profit_amount,
    maximal_consecutive_profit_count,
    profit_factor,
    recovery_factor,
    sharpe_ratio,
    short_trades,
    total_net_profit,
    total_trades,
)

# --- 参照仕様② MT5 校正版（再エクスポート）---
from simulator.usecase.mt5_parity import (  # noqa: F401
    average_loss_trade,
    average_profit_trade,
    equity_dd_absolute,
    equity_dd_maximal,
    equity_dd_maximal_percent,
    is_count_win,
    loss_trades,
    profit_long_trades,
    profit_short_trades,
    profit_trades,
    recovery_factor_equity,
    sharpe_ratio_per_trade,
    z_score,
)


# ---- 統合（結線: どちらの参照仕様を採るかの選択のみ） ----

def compute_stats(
    *,
    trades: Sequence[TradeRecord],
    balance_curve: Sequence[float],
    equity_curve: Sequence[float],
    initial_deposit: float,
) -> BacktestStats:
    """確定トレード列・balance/equity 系列から BacktestStats を算出する。

    実 MT5 整合（第2サイクルで結線・ISSUE-013）:
      * sharpe_ratio は per-trade profit 系列の Sharpe を [-5,5] にクランプした値
        （mt5_parity.sharpe_ratio_per_trade）。HPR 版 metrics_spec.sharpe_ratio() は残置
        （METRICS §1.2 用途）。
      * recovery_factor は equity DD 基準・符号付き net（mt5_parity.recovery_factor_equity）。
        equity_curve 未供給（空列）時は balance 基準 metrics_spec.recovery_factor() へ
        フォールバック（後方互換）。
      * equity 系 DD（equity_dd_abs / max / max_percent）は equity_curve から算出。
        equity_curve 未供給時は 0（後方互換）。
    balance 系 STAT_*（balance_dd 等）は不変（metrics_spec）。
    """
    has_equity = len(equity_curve) > 0
    recovery = (
        recovery_factor_equity(trades, equity_curve, initial_deposit)
        if has_equity
        else recovery_factor(trades, balance_curve, initial_deposit)
    )
    return BacktestStats(
        initial_deposit=float(initial_deposit),
        profit=total_net_profit(trades),
        gross_profit=gross_profit(trades),
        gross_loss=gross_loss(trades),
        profit_factor=profit_factor(trades),
        recovery_factor=recovery,
        expected_payoff=expected_payoff(trades),
        sharpe_ratio=sharpe_ratio_per_trade(trades),
        trades=total_trades(trades),
        profit_trades=profit_trades(trades),
        loss_trades=loss_trades(trades),
        long_trades=long_trades(trades),
        short_trades=short_trades(trades),
        profit_long_trades=profit_long_trades(trades),
        profit_short_trades=profit_short_trades(trades),
        balance_min=balance_min(balance_curve, initial_deposit),
        balance_dd=balance_dd_maximal(balance_curve, initial_deposit),
        balance_dd_percent=balance_dd_maximal_percent(balance_curve, initial_deposit),
        balance_dd_relative=balance_dd_relative_amount(balance_curve, initial_deposit),
        balance_ddrel_percent=balance_dd_relative_percent(balance_curve, initial_deposit),
        max_profit_trade=largest_profit_trade(trades),
        max_loss_trade=largest_loss_trade(trades),
        max_con_wins=max_consecutive_wins_count(trades),
        max_con_profit_trades=max_consecutive_wins_profit(trades),
        max_con_losses=max_consecutive_losses_count(trades),
        max_con_loss_trades=max_consecutive_losses_loss(trades),
        con_profit_max=maximal_consecutive_profit_amount(trades),
        con_profit_max_trades=maximal_consecutive_profit_count(trades),
        con_loss_max=maximal_consecutive_loss_amount(trades),
        con_loss_max_trades=maximal_consecutive_loss_count(trades),
        profit_trades_avg_con=average_consecutive_wins(trades),
        loss_trades_avg_con=average_consecutive_losses(trades),
        average_profit_trade=average_profit_trade(trades),
        average_loss_trade=average_loss_trade(trades),
        z_score=z_score(trades),
        ahpr=ahpr(balance_curve, initial_deposit),
        balance_dd_abs=balance_dd_absolute(balance_curve, initial_deposit),
        equity_dd_abs=(
            equity_dd_absolute(equity_curve, initial_deposit) if has_equity else 0.0
        ),
        equity_dd_max=(
            equity_dd_maximal(equity_curve, initial_deposit) if has_equity else 0.0
        ),
        equity_dd_max_percent=(
            equity_dd_maximal_percent(equity_curve, initial_deposit)
            if has_equity
            else 0.0
        ),
    )
