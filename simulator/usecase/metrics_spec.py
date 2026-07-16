"""参照仕様①: METRICS 文書版（BACKTEST_METRICS.md §1〜§6 準拠）の純粋関数群。

compute_stats() の参照仕様二重定義（ISSUE-094 🔵）を分離した片翼。本モジュールは
BACKTEST_METRICS.md の式を一次情報とする「文書版」統計（HPR Sharpe・balance 基準
recovery・AHPR/GHPR・balance ドローダウン・連勝/連敗ラン・§1 損益サマリー）を担う。

実 MT5 golden へ校正した別式（クランプ Sharpe・equity 符号付き recovery・equity DD・
MT5 件数規則 pnl>=0・MT5 Z-Score）は mt5_parity.py へ分離する。compute_stats() が
どちらの版を採るかの結線は compute_stats.py が担う（本モジュールは選択に関与しない）。

pandas は計算補助として許容するが numpy のみで完結する（依存最小化）。usecase 層は
domain のみ依存可。
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from simulator.domain.trade_record import TradeRecord


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


def is_run_win(t: TradeRecord) -> bool:
    """連勝ラン用の勝ち判定: pnl > 0（ゼロ損益はラン中立で勝ちに数えない）。

    根拠: METRICS §6.1（同値はラン区切り）/ §4.3（AvgConWins=N_w/K_w の N_w は
    win ラン内件数）。件数系 is_count_win(pnl>=0) とは基準が異なる。
    """
    return t.pnl() > 0


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


# ---- §3 件数（銘柄方向・spec 中立） ----

def total_trades(trades: Sequence[TradeRecord]) -> int:
    return len(trades)


def long_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if t.is_long())


def short_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if not t.is_long())


# ---- §4 個別トレード統計（最大・連勝/連敗ラン） ----

def largest_profit_trade(trades: Sequence[TradeRecord]) -> float:
    pnls = _pnls(trades)
    return float(max(pnls)) if pnls else 0.0


def largest_loss_trade(trades: Sequence[TradeRecord]) -> float:
    pnls = _pnls(trades)
    return float(min(pnls)) if pnls else 0.0


def _win_runs(trades: Sequence[TradeRecord]) -> list[list[TradeRecord]]:
    # ラン基準 is_run_win=pnl>0（ゼロは _runs で中立として既に除外済み）
    return [r for r in _runs(trades) if r and is_run_win(r[0])]


def _loss_runs(trades: Sequence[TradeRecord]) -> list[list[TradeRecord]]:
    return [r for r in _runs(trades) if r and not is_run_win(r[0])]


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
    # METRICS §4.3: AvgConWins = N_w / K_w（K_w=0 のとき 0）。
    # N_w = win ラン内のトレード数（is_run_win=pnl>0 でラン分割・ゼロはラン中立で区切る）。
    # 件数系 profit_trades(is_count_win=pnl>=0) を分子に流用してはならない（基準が異なる）。
    runs = _win_runs(trades)
    if not runs:
        return 0.0
    return sum(len(r) for r in runs) / len(runs)


def average_consecutive_losses(trades: Sequence[TradeRecord]) -> float:
    # METRICS §4.3: AvgConLosses = N_l / K_l。N_l = loss ラン内のトレード数。
    # loss_trades(pnl<0) と偶然一致する系列もあるが、対称性のためラン内件数で明示統一する。
    runs = _loss_runs(trades)
    if not runs:
        return 0.0
    return sum(len(r) for r in runs) / len(runs)
