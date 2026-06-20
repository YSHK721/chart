"""UC-002: STAT_* を確定トレード列・balance/equity 系列から決定論的に算出する。

METRICS §1〜§4 の式を一次情報とする純粋関数群。pandas は計算補助として許容するが、
本モジュールは numpy のみで完結する（依存最小化）。usecase 層は domain のみ依存可。

実 MT5 校正（ISSUE-013 / golden: tests/fixtures/mt5/ma_slope_jp225_202501/expected/report.json）:
    本プロジェクト目的＝MT5 再現につき、BACKTEST_METRICS.md と実 MT5 が割れる点は実 MT5 を正とする。
    * profit_trades = count(pnl >= 0)（ゼロ損益を勝ちに数える）。loss_trades = count(pnl < 0)。
      avg_profit = gross_profit / profit_trades(>=0)。avg_loss = gross_loss / loss_trades。
      （連勝/連敗ラン max_con_wins/losses の win 判定は is_win()=pnl>0・ゼロ中立で別ルール）
    * Z-Score = (N*(R-0.5) - P) / sqrt(P*(P-N)/(N-1)), P=2WL, W=count(pnl>=0), R=ラン数（pnl>=0/<0）。
      METRICS §3.2 の (R-E(R))/sqrt(Var(R)) 形は実 MT5（golden 2.35）と再現せず不採用。
    * AHPR/GHPR = HPR_i = B_i/B_{i-1}（= 1 + profit_i/balance_before_i と算術的に同値）。

実 MT5 校正済の equity 系 STAT_*（additive 追加・balance 系とは別関数で並存）:
    * STAT_SHARPE_RATIO (MT5 -5.0): per-trade profit 系列の (mean/std(ddof=0))×√N を
      MT5 の返却域 [-5, 5] にクランプ → sharpe_ratio_per_trade()。golden で素値 -5.08 →
      -5.0 を再現。HPR 版 sharpe_ratio()（ddof=0）は残置（METRICS §1.2 用途・golden 対象外）。
    * STAT_RECOVERY_FACTOR (MT5 -0.935547 = net/EquityDD_max(6594)): MT5 定義の EquityDD
      基準・符号付き net → recovery_factor_equity()。balance 基準 recovery_factor() は残置。
    * STAT_EQUITY_DD / EQUITY_DD_abs (6594 / 6174): equity_curve(含み損込み) 上の peak-to-
      trough DD → equity_dd_maximal() / equity_dd_absolute() / equity_dd_maximal_percent()。
      fixture deals に per-bar equity が無いため、golden は MT5 equity 安値(min=3826/peak=
      10420)を満たす構成 equity_curve を入力に MT5 値を再現する（test_compute_stats_golden_mt5）。

判断点（doc 不整合・upstream-input-validation で実証・据え置き）:
    * Sharpe / σ(HPR): METRICS §1.2/§11 の式（ddof=0 母分散）を採用。§12.2/§12.6 記載の
      σ=0.020019・Sharpe=0.17 は式から再現不能（実測 σ=0.018362・Sharpe=0.1862）のため不採用。
      実 MT5 の Sharpe=-5.0 はバー別 equity 要のため未決（上記参照）。§12 Sharpe 期待値（0.1862）は据え置き。
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from simulator.domain.trade_record import TradeRecord
from simulator.usecase.models import BacktestStats


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


def is_count_win(t: TradeRecord) -> bool:
    """件数系 / Z-Score 用の勝ち判定: pnl >= 0（ゼロ損益を勝ちに数える）。

    根拠: 実 MT5 実測（golden report_900005560 で profit_trades=292）。連勝/連敗ラン
    の勝ち判定 is_run_win(pnl>0) とは基準が異なるため、両者を混用してはならない。
    """
    return t.pnl() >= 0


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


def recovery_factor_equity(
    trades: Sequence[TradeRecord],
    equity_curve: Sequence[float],
    initial_deposit: float,
) -> float:
    # 実 MT5 定義: STAT_RECOVERY_FACTOR = TotalNetProfit / EquityDD_max。
    # balance 系 recovery_factor が |net| / Balance_DD なのに対し、MT5 実装は equity DD
    # 基準かつ符号付き net（純益が負なら負値）。golden で -6169/6594 = -0.935547 を再現。
    dd = equity_dd_maximal(equity_curve, initial_deposit)
    if dd == 0:
        return math.inf
    return total_net_profit(trades) / dd


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


# ---- §2 ドローダウン（Equity 系・実 MT5 校正） ----
# equity_curve はバー別 equity（含み損込み）。balance 系と同型の peak-to-trough DD を
# equity_curve に適用する。実 MT5 fixture(ma_slope_jp225_202501) の STAT_EQUITY_DD /
# STAT_EQUITY_DD_abs に校正（golden: equity_dd_max=6594 / equity_dd_abs=6174 / 63.28%）。
# 注: balance 系と共用の _full_balance が B_0(initial_deposit) を先頭付加する。METRICS §2.2
#   の equity DD 式は B_0 を prepend しないため、equity が一度も B_0 を上回らない（開始直後が
#   peak）ケースでは差が出うる。実 MT5 ケースは peak(≈10420) > B_0(10000) で一致するため
#   現校正は不変。equity 全点 < B_0 の銘柄では仕様差に留意（既知の仕様差）。

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


# ---- §3 件数・分布 ----

def total_trades(trades: Sequence[TradeRecord]) -> int:
    return len(trades)


def profit_trades(trades: Sequence[TradeRecord]) -> int:
    # 実 MT5 定義: profit_trades = count(is_count_win=pnl>=0)。ゼロ損益トレードを「勝ち」に数える。
    # （連勝/連敗ランの win 判定 is_run_win=pnl>0 とは別ルール。golden report_900005560 で 292 を再現）
    return sum(1 for t in trades if is_count_win(t))


def loss_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if t.pnl() < 0)


def long_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if t.is_long())


def short_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if not t.is_long())


def profit_long_trades(trades: Sequence[TradeRecord]) -> int:
    # MT5 profit 系件数は is_count_win=pnl>=0 を「勝ち」に数える（profit_trades と同ルール）
    return sum(1 for t in trades if t.is_long() and is_count_win(t))


def profit_short_trades(trades: Sequence[TradeRecord]) -> int:
    return sum(1 for t in trades if not t.is_long() and is_count_win(t))


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


# ---- §4 個別トレード統計 ----

def largest_profit_trade(trades: Sequence[TradeRecord]) -> float:
    pnls = _pnls(trades)
    return float(max(pnls)) if pnls else 0.0


def largest_loss_trade(trades: Sequence[TradeRecord]) -> float:
    pnls = _pnls(trades)
    return float(min(pnls)) if pnls else 0.0


def average_profit_trade(trades: Sequence[TradeRecord]) -> float:
    # 実 MT5 定義: gross_profit / profit_trades(pnl>=0)。ゼロ損益は分母（件数）に算入し
    # 分子（gross_profit）には 0 寄与する。golden report_900005560 で 35.979452 を再現。
    n = profit_trades(trades)
    return float(gross_profit(trades) / n) if n else 0.0


def average_loss_trade(trades: Sequence[TradeRecord]) -> float:
    # 実 MT5 定義: gross_loss / loss_trades(pnl<0)。
    n = loss_trades(trades)
    return float(gross_loss(trades) / n) if n else 0.0


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


# ---- 統合 ----

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
        （sharpe_ratio_per_trade）。HPR 版 sharpe_ratio() は残置（METRICS §1.2 用途）。
      * recovery_factor は equity DD 基準・符号付き net（recovery_factor_equity）。
        equity_curve 未供給（空列）時は balance 基準 recovery_factor() へフォールバック
        （後方互換）。
      * equity 系 DD（equity_dd_abs / max / max_percent）は equity_curve から算出。
        equity_curve 未供給時は 0（後方互換）。
    balance 系 STAT_*（balance_dd 等）は不変。
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
