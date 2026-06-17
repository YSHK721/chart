"""UC-002: STAT_* を確定トレード列・balance/equity 系列から決定論的に算出する。

METRICS §1〜§4 の式を一次情報とする純粋関数群。pandas は計算補助として許容するが、
本モジュールは numpy のみで完結する（依存最小化）。usecase 層は domain のみ依存可。

判断点（doc 不整合・upstream-input-validation で実証）:
    * Sharpe / σ(HPR): METRICS §1.2/§11 の式（ddof=0 母分散）を採用。§12.2/§12.6 記載の
      σ=0.020019・Sharpe=0.17 は式から再現不能（実測 σ=0.018362・Sharpe=0.1862）のため不採用。
    * Z-Score: METRICS §3.2 数式 Z=(R-E(R))/sqrt(Var(R)) を採用。§11 ヘルパーの分母疑義を上書き。
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from backtest.domain.trade_record import TradeRecord
from backtest.usecase.models import BacktestStats


# ---- 内部ヘルパー ----

def _pnls(trades: Sequence[TradeRecord]) -> list[float]:
    return [t.pnl() for t in trades]


def _hpr_series(balance_curve: Sequence[float], initial_deposit: float) -> list[float]:
    """HPR_i = B_i / B_{i-1}（B_0 = initial_deposit）。

    METRICS §1.4: B_i <= 0（直前バランス）は HPR 定義不能のためスキップする。
    """
    hpr: list[float] = []
    prev = initial_deposit
    for b in balance_curve:
        if prev <= 0:
            prev = b
            continue
        hpr.append(b / prev)
        prev = b
    return hpr


def _sign(t: TradeRecord) -> int:
    """トレードの符号 sign(pnl) ∈ {+1, 0, -1}（PROCESS §6.1: 同値は 0）。"""
    p = t.pnl()
    if p > 0:
        return 1
    if p < 0:
        return -1
    return 0


def _runs(trades: Sequence[TradeRecord]) -> list[list[TradeRecord]]:
    """連勝/連敗のラン列へ 3 値分割する（PROCESS §6.1:349-350）。

    各トレードを sign(pnl)∈{+1,0,-1} で分類し、同符号の連続をランとする。
    同値（pnl=0）はどのランにも属さず、前後のランを区切る（カウントをリセット）。
    """
    runs: list[list[TradeRecord]] = []
    broken = False  # 直前に同値（0）が現れランを区切った
    for t in trades:
        s = _sign(t)
        if s == 0:
            broken = True  # 同値はランに属さず、前後を区切る
            continue
        if runs and not broken and _sign(runs[-1][0]) == s:
            runs[-1].append(t)
        else:
            runs.append([t])
        broken = False
    return runs


# ---- §1 損益サマリー ----

def total_net_profit(trades: Sequence[TradeRecord]) -> float:
    return float(sum(_pnls(trades)))


def gross_profit(trades: Sequence[TradeRecord]) -> float:
    return float(sum(p for p in _pnls(trades) if p > 0))


def gross_loss(trades: Sequence[TradeRecord]) -> float:
    return float(sum(p for p in _pnls(trades) if p < 0))


def profit_factor(trades: Sequence[TradeRecord]) -> float:
    gl = gross_loss(trades)
    if gl == 0:
        return math.inf  # METRICS §1.1: GrossLoss == 0 のとき ∞
    return gross_profit(trades) / abs(gl)


def expected_payoff(trades: Sequence[TradeRecord]) -> float:
    n = len(trades)
    if n == 0:
        return 0.0
    return total_net_profit(trades) / n


def ahpr(balance_curve: Sequence[float], initial_deposit: float) -> float:
    hpr = _hpr_series(balance_curve, initial_deposit)
    if not hpr:
        return 0.0
    return float(np.mean(hpr))


def ghpr(balance_curve: Sequence[float], initial_deposit: float) -> float:
    hpr = _hpr_series(balance_curve, initial_deposit)
    if not hpr:
        return 0.0
    return float(np.prod(hpr) ** (1.0 / len(hpr)))


def sharpe_ratio(balance_curve: Sequence[float], initial_deposit: float) -> float:
    # METRICS §1.2: (mean(HPR) - 1) / std(HPR, ddof=0)。σ==0 のとき 0。
    hpr = _hpr_series(balance_curve, initial_deposit)
    if not hpr:
        return 0.0
    arr = np.asarray(hpr, dtype=float)
    sigma = float(arr.std(ddof=0))
    if sigma == 0:
        return 0.0
    return (float(arr.mean()) - 1.0) / sigma


def recovery_factor(
    trades: Sequence[TradeRecord],
    balance_curve: Sequence[float],
    initial_deposit: float,
) -> float:
    # METRICS §1.1: |TotalNetProfit| / Balance_DD_Max($)。DD==0 なら ∞。
    dd = balance_dd_maximal(balance_curve, initial_deposit)
    if dd == 0:
        return math.inf
    return abs(total_net_profit(trades)) / dd


# ---- §2 ドローダウン（Balance 系） ----

def _full_balance(balance_curve: Sequence[float], initial_deposit: float) -> np.ndarray:
    """B_0 を先頭に含む系列（peak 走査用）。"""
    return np.asarray([initial_deposit, *balance_curve], dtype=float)


def balance_min(balance_curve: Sequence[float], initial_deposit: float) -> float:
    return float(_full_balance(balance_curve, initial_deposit).min())


def balance_dd_absolute(balance_curve: Sequence[float], initial_deposit: float) -> float:
    # METRICS §2.2: B_0 - min_k B_k
    return initial_deposit - balance_min(balance_curve, initial_deposit)


def _dd_arrays(balance_curve: Sequence[float], initial_deposit: float):
    b = _full_balance(balance_curve, initial_deposit)
    peak = np.maximum.accumulate(b)
    dd_abs = peak - b
    dd_pct = np.where(peak != 0, dd_abs / peak * 100.0, 0.0)
    return dd_abs, dd_pct


def balance_dd_maximal(balance_curve: Sequence[float], initial_deposit: float) -> float:
    dd_abs, _ = _dd_arrays(balance_curve, initial_deposit)
    return float(dd_abs.max())


def balance_dd_maximal_percent(balance_curve: Sequence[float], initial_deposit: float) -> float:
    # METRICS §2.2: 金額 DD を最大化する k での % DD
    dd_abs, dd_pct = _dd_arrays(balance_curve, initial_deposit)
    return float(dd_pct[int(dd_abs.argmax())])


def balance_dd_relative_percent(balance_curve: Sequence[float], initial_deposit: float) -> float:
    _, dd_pct = _dd_arrays(balance_curve, initial_deposit)
    return float(dd_pct.max())


def balance_dd_relative_amount(balance_curve: Sequence[float], initial_deposit: float) -> float:
    # METRICS §2.2: % DD を最大化する k での金額 DD
    dd_abs, dd_pct = _dd_arrays(balance_curve, initial_deposit)
    return float(dd_abs[int(dd_pct.argmax())])


# ---- §3 件数・分布 ----

def total_trades(trades: Sequence[TradeRecord]) -> int:
    return len(trades)


def profit_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if t.is_win())


def loss_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if t.pnl() < 0)


def long_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if t.is_long())


def short_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if not t.is_long())


def profit_long_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if t.is_long() and t.is_win())


def profit_short_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if not t.is_long() and t.is_win())


def z_score(trades: Sequence[TradeRecord]) -> float:
    # METRICS §3.2 Wald-Wolfowitz Runs Test
    n = len(trades)
    nw = profit_trades(trades)
    nl = n - nw
    if nw == 0 or nl == 0 or n < 2:
        return 0.0
    runs = _runs(trades)
    r = len(runs)
    er = 2 * nw * nl / n + 1
    var = 2 * nw * nl * (2 * nw * nl - n) / (n**2 * (n - 1))
    if var <= 0:
        return 0.0
    return (r - er) / math.sqrt(var)


# ---- §4 個別トレード統計 ----

def largest_profit_trade(trades: Sequence[TradeRecord]) -> float:
    pnls = _pnls(trades)
    return float(max(pnls)) if pnls else 0.0


def largest_loss_trade(trades: Sequence[TradeRecord]) -> float:
    pnls = _pnls(trades)
    return float(min(pnls)) if pnls else 0.0


def average_profit_trade(trades: Sequence[TradeRecord]) -> float:
    wins = [p for p in _pnls(trades) if p > 0]
    return float(sum(wins) / len(wins)) if wins else 0.0


def average_loss_trade(trades: Sequence[TradeRecord]) -> float:
    losses = [p for p in _pnls(trades) if p < 0]
    return float(sum(losses) / len(losses)) if losses else 0.0


def _win_runs(trades: Sequence[TradeRecord]) -> list[list[TradeRecord]]:
    return [r for r in _runs(trades) if r and r[0].is_win()]


def _loss_runs(trades: Sequence[TradeRecord]) -> list[list[TradeRecord]]:
    return [r for r in _runs(trades) if r and not r[0].is_win()]


def _run_profit(run: Sequence[TradeRecord]) -> float:
    return float(sum(t.pnl() for t in run))


def max_consecutive_wins_count(trades: Sequence[TradeRecord]) -> int:
    runs = _win_runs(trades)
    return max((len(r) for r in runs), default=0)


def max_consecutive_wins_profit(trades: Sequence[TradeRecord]) -> float:
    # 最長連勝ラン（同じ区間）の利益
    runs = _win_runs(trades)
    if not runs:
        return 0.0
    best = max(runs, key=len)
    return _run_profit(best)


def max_consecutive_losses_count(trades: Sequence[TradeRecord]) -> int:
    runs = _loss_runs(trades)
    return max((len(r) for r in runs), default=0)


def max_consecutive_losses_loss(trades: Sequence[TradeRecord]) -> float:
    runs = _loss_runs(trades)
    if not runs:
        return 0.0
    best = max(runs, key=len)
    return _run_profit(best)


def maximal_consecutive_profit_amount(trades: Sequence[TradeRecord]) -> float:
    runs = _win_runs(trades)
    if not runs:
        return 0.0
    return max(_run_profit(r) for r in runs)


def maximal_consecutive_profit_count(trades: Sequence[TradeRecord]) -> int:
    runs = _win_runs(trades)
    if not runs:
        return 0
    best = max(runs, key=_run_profit)
    return len(best)


def maximal_consecutive_loss_amount(trades: Sequence[TradeRecord]) -> float:
    # |profit(run)| を最大化するラン区間の損失（符号付き負値）
    runs = _loss_runs(trades)
    if not runs:
        return 0.0
    best = max(runs, key=lambda r: abs(_run_profit(r)))
    return _run_profit(best)


def maximal_consecutive_loss_count(trades: Sequence[TradeRecord]) -> int:
    runs = _loss_runs(trades)
    if not runs:
        return 0
    best = max(runs, key=lambda r: abs(_run_profit(r)))
    return len(best)


def average_consecutive_wins(trades: Sequence[TradeRecord]) -> float:
    # METRICS §4.3: N_w / K_w（K_w=0 のとき 0）
    runs = _win_runs(trades)
    if not runs:
        return 0.0
    return profit_trades(trades) / len(runs)


def average_consecutive_losses(trades: Sequence[TradeRecord]) -> float:
    runs = _loss_runs(trades)
    if not runs:
        return 0.0
    return loss_trades(trades) / len(runs)


# ---- 統合 ----

def compute_stats(
    *,
    trades: Sequence[TradeRecord],
    balance_curve: Sequence[float],
    equity_curve: Sequence[float],
    initial_deposit: float,
) -> BacktestStats:
    """確定トレード列・balance/equity 系列から BacktestStats を算出する。

    equity_curve は将来の Equity 系 DD 用に受けるが、本サイクルでは確定値のみを
    扱うため Balance 系 STAT_* を確定する（Equity 系は次サイクルで充填）。
    """
    return BacktestStats(
        initial_deposit=float(initial_deposit),
        profit=total_net_profit(trades),
        gross_profit=gross_profit(trades),
        gross_loss=gross_loss(trades),
        profit_factor=profit_factor(trades),
        recovery_factor=recovery_factor(trades, balance_curve, initial_deposit),
        expected_payoff=expected_payoff(trades),
        sharpe_ratio=sharpe_ratio(balance_curve, initial_deposit),
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
    )
