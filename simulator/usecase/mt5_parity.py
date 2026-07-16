"""参照仕様②: MT5 校正版（golden parity 準拠）の純粋関数群。

compute_stats() の参照仕様二重定義（ISSUE-094 🔵）を分離した片翼。本モジュールは
実 MT5（golden: tests/fixtures/mt5/ma_slope_jp225_202501/expected/report.json）へ校正
した「MT5 版」統計を担う。BACKTEST_METRICS.md と実 MT5 が割れる点は実 MT5 を正とする
（本プロジェクト目的＝MT5 再現）。

担当する MT5 校正統計:
    * 件数規則 pnl>=0（is_count_win）: profit_trades = count(pnl >= 0)。ゼロ損益を勝ちに
      数える。loss_trades = count(pnl < 0)。profit_long/short も同規則。
      （連勝/連敗ラン max_con_wins/losses の win 判定は metrics_spec.is_run_win=pnl>0・別ルール）
    * average_profit_trade = gross_profit / profit_trades(>=0)。avg_loss = gross_loss / loss_trades。
    * STAT_SHARPE_RATIO (golden -5.0): per-trade profit 系列の (mean/std(ddof=0))×√N を
      MT5 返却域 [-5, 5] にクランプ → sharpe_ratio_per_trade()。素値 -5.08 → -5.0 を再現。
      ※クランプ [-5, 5] は観測値説明の**仮説**（MT5 公式クランプ仕様は未確認・出典 TBD・ISSUE-013）。
    * STAT_RECOVERY_FACTOR (golden -0.935547 = net/EquityDD_max): equity DD 基準・符号付き
      net → recovery_factor_equity()。balance 基準は metrics_spec.recovery_factor()。
    * STAT_EQUITY_DD / EQUITY_DD_abs (6594 / 6174): equity_curve(含み損込み) 上の peak-to-
      trough DD → equity_dd_maximal() / equity_dd_absolute() / equity_dd_maximal_percent()。
    * Z-Score (golden 2.35): Wald-Wolfowitz Z=(N*(R-0.5)-P)/sqrt(P*(P-N)/(N-1))。
      P=2WL, W=count(pnl>=0), R=ラン数（pnl>=0/<0）。METRICS §3.2 の (R-E(R))/sqrt(Var(R))
      形は実 MT5 と再現せず不採用。

§1/§2 の共通下位関数（_pnls・gross_profit/loss・total_net_profit・_full_balance・
_dd_arrays）は metrics_spec.py から再利用する。
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from simulator.domain.trade_record import TradeRecord
from simulator.usecase.metrics_spec import (
    _dd_arrays,
    _full_balance,
    _pnls,
    gross_loss,
    gross_profit,
    total_net_profit,
)


# ---- 件数規則（pnl>=0 を勝ちに数える MT5 校正） ----

def is_count_win(t: TradeRecord) -> bool:
    """件数系 / Z-Score 用の勝ち判定: pnl >= 0（ゼロ損益を勝ちに数える）。

    根拠: 実 MT5 実測（golden report_900005560 で profit_trades=292）。連勝/連敗ラン
    の勝ち判定 metrics_spec.is_run_win(pnl>0) とは基準が異なるため、両者を混用してはならない。
    """
    return t.pnl() >= 0


def profit_trades(trades: Sequence[TradeRecord]) -> int:
    # 実 MT5 定義: profit_trades = count(is_count_win=pnl>=0)。ゼロ損益トレードを「勝ち」に数える。
    # （連勝/連敗ランの win 判定 is_run_win=pnl>0 とは別ルール。golden report_900005560 で 292 を再現）
    return sum(1 for t in trades if is_count_win(t))


def loss_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if t.pnl() < 0)


def profit_long_trades(trades: Sequence[TradeRecord]) -> int:
    # MT5 profit 系件数は is_count_win=pnl>=0 を「勝ち」に数える（profit_trades と同ルール）
    return sum(1 for t in trades if t.is_long() and is_count_win(t))


def profit_short_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if not t.is_long() and is_count_win(t))


# ---- 個別トレード平均（MT5 校正: 分母は pnl>=0 / pnl<0 件数） ----

def average_profit_trade(trades: Sequence[TradeRecord]) -> float:
    # 実 MT5 定義: gross_profit / profit_trades(pnl>=0)。ゼロ損益は分母（件数）に算入し
    # 分子（gross_profit）には 0 寄与する。golden report_900005560 で 35.979452 を再現。
    n = profit_trades(trades)
    return float(gross_profit(trades) / n) if n else 0.0


def average_loss_trade(trades: Sequence[TradeRecord]) -> float:
    # 実 MT5 定義: gross_loss / loss_trades(pnl<0)。
    n = loss_trades(trades)
    return float(gross_loss(trades) / n) if n else 0.0


# ---- Sharpe（per-trade profit・[-5,5] クランプ） ----

def sharpe_ratio_per_trade(trades: Sequence[TradeRecord]) -> float:
    # 実 MT5 STAT_SHARPE_RATIO = -5.000000（6桁ちょうど）。per-trade profit 系列の
    # Sharpe を (mean / std(ddof=0)) × √N で算出し [-5, 5] にクランプする。
    # ※クランプ [-5, 5] は「素値 -5.0838 → 観測値 -5.000000ちょうど」を説明する**仮説**で、
    #   MT5 のクランプ仕様は一次情報（MT5公式/ソース）未確認（出典TBD・ISSUE-013参照）。
    #   素値が [-5,5] 内ならクランプ非発火でそのまま返す（中間値ケース検証済）。
    # σ==0（全トレード同額）/ trades 空は 0.0（ゼロ除算・空列回避）。
    # golden(ma_slope_jp225_202501): 素値 -5.0838 → クランプ後 -5.0（MT5 一致）。
    pnls = _pnls(trades)
    if not pnls:
        return 0.0
    arr = np.asarray(pnls, dtype=float)
    sigma = float(arr.std(ddof=0))
    if sigma == 0:
        return 0.0
    raw = float(arr.mean()) / sigma * math.sqrt(len(arr))
    return max(-5.0, min(5.0, raw))


# ---- Recovery（equity DD 基準・符号付き net） ----

def recovery_factor_equity(
    trades: Sequence[TradeRecord],
    equity_curve: Sequence[float],
    initial_deposit: float,
) -> float:
    # 実 MT5 定義: STAT_RECOVERY_FACTOR = TotalNetProfit / EquityDD_max。
    # balance 系 metrics_spec.recovery_factor が |net| / Balance_DD なのに対し、MT5 実装は
    # equity DD 基準かつ符号付き net（純益が負なら負値）。golden で -6169/6594 = -0.935547 を再現。
    dd = equity_dd_maximal(equity_curve, initial_deposit)
    if dd == 0:
        return math.inf
    return total_net_profit(trades) / dd


# ---- ドローダウン（Equity 系・実 MT5 校正） ----
# equity_curve はバー別 equity（含み損込み）。balance 系と同型の peak-to-trough DD を
# equity_curve に適用する。実 MT5 fixture(ma_slope_jp225_202501) の STAT_EQUITY_DD /
# STAT_EQUITY_DD_abs に校正（golden: equity_dd_max=6594 / equity_dd_abs=6174 / 63.28%）。
# 注: balance 系と共用の metrics_spec._full_balance が B_0(initial_deposit) を先頭付加する。
#   METRICS §2.2 の equity DD 式は B_0 を prepend しないため、equity が一度も B_0 を上回らない
#   （開始直後が peak）ケースでは差が出うる。実 MT5 ケースは peak(≈10420) > B_0(10000) で一致
#   するため現校正は不変。equity 全点 < B_0 の銘柄では仕様差に留意（既知の仕様差）。

def equity_dd_absolute(equity_curve: Sequence[float], initial_deposit: float) -> float:
    # METRICS §2.2 と同型: B_0(initial_deposit) - min_k equity_k。
    return initial_deposit - float(_full_balance(equity_curve, initial_deposit).min())


def equity_dd_maximal(equity_curve: Sequence[float], initial_deposit: float) -> float:
    # peak-to-trough の最大金額 DD（含み損込み equity 上）。
    dd_abs, _ = _dd_arrays(equity_curve, initial_deposit)
    return float(dd_abs.max())


def equity_dd_maximal_percent(equity_curve: Sequence[float], initial_deposit: float) -> float:
    # 金額 DD を最大化する k での % DD（balance 系 balance_dd_maximal_percent と同型）。
    dd_abs, dd_pct = _dd_arrays(equity_curve, initial_deposit)
    return float(dd_pct[int(dd_abs.argmax())])


# ---- Z-Score（Wald-Wolfowitz・pnl>=0/<0 の 2 値ラン） ----

def _z_run_count(trades: Sequence[TradeRecord]) -> int:
    """Z-Score 用のラン数 R（2 値分割: win=pnl>=0 / loss=pnl<0）。

    連勝/連敗の最長ラン（is_win()=pnl>0・ゼロ中立）とは別ルール。MT5 Z-Score は
    profit_trades と同じく pnl>=0 を勝ち側にまとめて連を数える（golden で 468 を再現）。
    """
    r = 0
    prev: bool | None = None
    for t in trades:
        cur = is_count_win(t)  # Z は件数系と同じ pnl>=0 基準でラン分割
        if cur != prev:
            r += 1
            prev = cur
    return r


def z_score(trades: Sequence[TradeRecord]) -> float:
    # 実 MT5 Wald-Wolfowitz: Z = (N*(R-0.5) - P) / sqrt(P*(P-N)/(N-1))
    #   W = profit_trades(pnl>=0), L = loss_trades(pnl<0), N = W+L, P = 2*W*L,
    #   R = ラン数（win=pnl>=0 / loss=pnl<0 の 2 値分割）。
    # METRICS §3.2 の (R-E(R))/sqrt(Var(R)) 形は §12 で 1.6771（本式）と一致せず（1.3416）、
    # 本プロジェクト目的（MT5 再現）に従い実 MT5 値（golden 2.35）を正とする。
    w = profit_trades(trades)
    n = len(trades)
    l = n - w
    if w == 0 or l == 0 or n < 2:
        return 0.0
    p = 2 * w * l
    denom_sq = p * (p - n) / (n - 1)
    if denom_sq <= 0:
        return 0.0
    r = _z_run_count(trades)
    return (n * (r - 0.5) - p) / math.sqrt(denom_sq)
