"""UC-002 compute_stats: 実 MT5 レポート（report_900005560.json）への golden 突合。

ground truth は MT5 ストラテジーテスターが出力した STAT_*（fixture `results`）。
本テストは fixture の `deals`（dir=="out" の往復確定トレード 1163 件）から
TradeRecord 列・balance 曲線・initial_deposit を再構成し、compute_stats を通して
MT5 実測値を再現することを assert する。

非トートロジー性: 期待値は fixture `results`（MT5 実測オラクル）を参照するか、MT5
実測値を固定値として直書きする（いずれも実装の出力をなぞらない）。max_consecutive_wins
/max_consecutive_losses/ahpr は fixture `results` を直接参照しオラクル由来であることを保証する。

許容誤差（依頼仕様）: 金額 ±0.5・比率 ±1e-4・件数完全一致。

再現可能な STAT_*（19 項目）のみを対象とする。以下 4 項目は fixture の `deals` に
**バー別 equity 系列が無い**ため、トレード/balance 列だけからは再現不能であり golden
対象から除外する（ISSUE-013 / compute_stats docstring に理由を明記）:
    * STAT_SHARPE_RATIO (-5.0)        — 要バー別 equity 収益率
    * STAT_EQUITY_DD ($) (6594)       — 要ティック別 equity（含み損ピーク）
    * STAT_EQUITY_DD_abs (6174)       — 同上
    * STAT_RECOVERY_FACTOR (-0.935547)— MT5 定義 = net / EquityDD_max。EquityDD 不能のため除外
"""
from __future__ import annotations

import pytest

from simulator.domain.trade_record import TradeRecord
from simulator.tests.fixtures.mt5 import load_case
from simulator.usecase.compute_stats import compute_stats

_CASE_NAME = "ma_slope_jp225_202501"


def _load_fixture() -> dict:
    # ケース単位の自己完結 fixture (fixtures/mt5/<case>/expected/report.json) を
    # 統一ローダ経由で参照する。期待値・assert は不変。
    return load_case(_CASE_NAME).expected


def _reconstruct_trades(deals: list[dict]) -> list[TradeRecord]:
    """dir=="out" の往復確定トレードから TradeRecord 列を再構成する。

    各 out deal は profit（往復確定損益）と closing side（type）を持つ。
    entry side = closing side の反対（sell で閉じれば元は buy=long）。
    pnl() が profit を再現するよう entry/exit price を構成する
    （contract_size=1 / volume=1 / swap=commission=0、(exit-entry)*sign == profit）。
    """
    trades: list[TradeRecord] = []
    for i, dl in enumerate(deals):
        if dl.get("dir") != "out":
            continue
        profit = float(dl["profit"])
        entry_side = "buy" if dl["type"] == "sell" else "sell"
        sign = 1 if entry_side == "buy" else -1
        entry = 1000.0
        exit_price = entry + profit / sign  # (exit-entry)*sign == profit
        trades.append(
            TradeRecord(
                side=entry_side,
                volume=1.0,
                entry_time=i,
                exit_time=i + 1,
                entry_price=entry,
                exit_price=exit_price,
                contract_size=1.0,
                swap=0.0,
                commission=0.0,
                exit_reason="tp" if profit >= 0 else "sl",
            )
        )
    return trades


def _reconstruct_balance_curve(deals: list[dict]) -> list[float]:
    return [float(dl["balance"]) for dl in deals if dl.get("dir") == "out"]


@pytest.fixture(scope="module")
def golden_stats():
    fx = _load_fixture()
    deals = fx["deals"]
    trades = _reconstruct_trades(deals)
    balance_curve = _reconstruct_balance_curve(deals)
    initial_deposit = float(fx["settings"]["initial_deposit"])
    stats = compute_stats(
        trades=trades,
        balance_curve=balance_curve,
        equity_curve=balance_curve,  # 代替（ティック別 equity は fixture に無い）
        initial_deposit=initial_deposit,
    )
    return stats, fx["results"]


# ---- §1 損益サマリー ----

def test_golden_total_net_profit(golden_stats):
    stats, _ = golden_stats
    assert stats.profit == pytest.approx(-6169.0, abs=0.5)


def test_golden_gross_profit(golden_stats):
    stats, _ = golden_stats
    assert stats.gross_profit == pytest.approx(10506.0, abs=0.5)


def test_golden_gross_loss(golden_stats):
    stats, _ = golden_stats
    assert stats.gross_loss == pytest.approx(-16675.0, abs=0.5)


def test_golden_profit_factor(golden_stats):
    stats, _ = golden_stats
    assert stats.profit_factor == pytest.approx(0.630045, abs=1e-4)


def test_golden_expected_payoff(golden_stats):
    stats, _ = golden_stats
    assert stats.expected_payoff == pytest.approx(-5.304385, abs=1e-4)


# ---- §3 件数・分布 ----

def test_golden_total_trades(golden_stats):
    stats, _ = golden_stats
    assert stats.trades == 1163


def test_golden_profit_trades_counts_zero_pnl_as_win(golden_stats):
    # MT5: profit_trades = count(profit >= 0)（ゼロ損益を勝ちに数える）
    stats, _ = golden_stats
    assert stats.profit_trades == 292


def test_golden_loss_trades(golden_stats):
    stats, _ = golden_stats
    assert stats.loss_trades == 871


def test_golden_long_trades(golden_stats):
    stats, _ = golden_stats
    assert stats.long_trades == 582


def test_golden_short_trades(golden_stats):
    stats, _ = golden_stats
    assert stats.short_trades == 581


def test_golden_z_score(golden_stats):
    # MT5 Z = (N(R-0.5) - P) / sqrt(P(P-N)/(N-1)), P=2WL, W=profit>=0
    stats, _ = golden_stats
    assert stats.z_score == pytest.approx(2.35, abs=0.01)


# ---- §4 個別トレード統計 ----

def test_golden_largest_profit_trade(golden_stats):
    stats, _ = golden_stats
    assert stats.max_profit_trade == pytest.approx(245.0, abs=0.5)


def test_golden_largest_loss_trade(golden_stats):
    stats, _ = golden_stats
    assert stats.max_loss_trade == pytest.approx(-130.0, abs=0.5)


def test_golden_average_profit_trade(golden_stats):
    # MT5 avg_profit = gross_profit / profit_trades(>=0) = 10506 / 292
    stats, _ = golden_stats
    assert stats.average_profit_trade == pytest.approx(35.979452, abs=1e-4)


def test_golden_average_loss_trade(golden_stats):
    stats, _ = golden_stats
    assert stats.average_loss_trade == pytest.approx(-19.144661, abs=1e-4)


def test_golden_max_consecutive_wins(golden_stats):
    # 期待値は fixture results（実 MT5 オラクル）を参照し非トートロジー化する
    stats, results = golden_stats
    assert stats.max_con_wins == results["max_consecutive_wins"]


def test_golden_max_consecutive_losses(golden_stats):
    stats, results = golden_stats
    assert stats.max_con_losses == results["max_consecutive_losses"]


# ---- AHPR / GHPR ----

def test_golden_ahpr(golden_stats):
    # MT5 AHPR = mean(1 + profit_i / balance_before_i)。期待値は fixture results を参照
    stats, results = golden_stats
    assert stats.ahpr == pytest.approx(results["ahpr"], abs=1e-4)


# ---- §2 ドローダウン（Balance 系） ----

def test_golden_balance_dd_absolute(golden_stats):
    stats, _ = golden_stats
    assert stats.balance_dd_abs == pytest.approx(6169.0, abs=0.5)


def test_golden_balance_dd_maximal_amount(golden_stats):
    stats, _ = golden_stats
    assert stats.balance_dd == pytest.approx(6476.0, abs=0.5)


def test_golden_balance_dd_maximal_percent(golden_stats):
    stats, _ = golden_stats
    assert stats.balance_dd_percent == pytest.approx(62.83, abs=0.01)


# ---- equity 系 STAT_*（実 MT5 校正） -----------------------------------------
# Sharpe（per-trade・clamp）と recovery（net/EquityDD）は MT5 約定列（_reconstruct_trades）
# 由来で非トートロジーに突合する。
#
# equity-DD（abs/max/max_percent）の「engine equity_curve 実走突合」は本 unit fixture
# （deals に per-bar equity 無し）では構成不能のため、逆算 equity_curve による golden は
# トートロジー（入力で出力を逆算）となる。よって equity-DD の非トートロジー突合は
# integration テスト tests/integration/test_ma_slope_reconcile.py::
# TestMaSlopeEquityStatsReconcile（engine 実走 equity_curve で実測 vs MT5 を残差付き突合）に
# 移譲する。本ファイルでは equity-DD 関数の純粋単体性（境界値）と Sharpe/recovery の
# MT5 約定列突合のみを担う（トートロジー golden は撤去済）。
_MT5_INITIAL_DEPOSIT = 10_000.0


def test_golden_sharpe_ratio_per_trade_clamped_to_minus_five():
    # MT5 STAT_SHARPE_RATIO = -5.0。per-trade profit 系列の (mean/std)×√N = -5.08 を
    # [-5, 5] にクランプして MT5 値を再現する。期待値は report.json results を参照。
    from simulator.usecase.compute_stats import sharpe_ratio_per_trade

    fx = _load_fixture()
    trades = _reconstruct_trades(fx["deals"])
    assert sharpe_ratio_per_trade(trades) == pytest.approx(
        fx["results"]["sharpe_ratio_mt5"], abs=1e-9
    )


def test_sharpe_ratio_per_trade_clamps_positive_to_upper_bound():
    # [-5, 5] の上限クランプ: 強い正の系列で +5 を超える素の値を 5.0 にクランプする。
    from simulator.usecase.compute_stats import sharpe_ratio_per_trade

    trades = [
        TradeRecord(
            side="buy", volume=1.0, entry_time=i, exit_time=i + 1,
            entry_price=1000.0, exit_price=1000.0 + (100.0 if i % 2 else 101.0),
            contract_size=1.0, swap=0.0, commission=0.0, exit_reason="tp",
        )
        for i in range(50)
    ]
    assert sharpe_ratio_per_trade(trades) == pytest.approx(5.0, abs=1e-9)


def test_sharpe_ratio_per_trade_zero_std_returns_zero():
    # 全トレード同額（std==0）でゼロ除算を回避し 0.0 を返す。
    from simulator.usecase.compute_stats import sharpe_ratio_per_trade

    trades = [
        TradeRecord(
            side="buy", volume=1.0, entry_time=i, exit_time=i + 1,
            entry_price=1000.0, exit_price=1010.0,
            contract_size=1.0, swap=0.0, commission=0.0, exit_reason="tp",
        )
        for i in range(10)
    ]
    assert sharpe_ratio_per_trade(trades) == 0.0


def test_sharpe_ratio_per_trade_empty_returns_zero():
    from simulator.usecase.compute_stats import sharpe_ratio_per_trade

    assert sharpe_ratio_per_trade([]) == 0.0


def test_equity_dd_functions_are_pure_peak_to_trough():
    # equity-DD 関数の純粋性（単体・非トートロジー）: 任意の既知 equity_curve に対し
    # 「peak-to-trough 最大金額 DD」「init - min」「金額最大点での %」を正しく計算する。
    # 期待値は curve から人手で導いた独立計算（MT5 オラクルとは独立の純関数性質）。
    #   curve=[120, 80, 150, 60, 90], init=100 → full=[100,120,80,150,60,90]
    #   peak 走査: 100,120,120,150,150,150 / dd_abs: 0,0,40,0,90,60 → max=90（150-60）
    #   init - min = 100 - 60 = 40 / % at max-amount point = 90/150*100 = 60.0
    from simulator.usecase.compute_stats import (
        equity_dd_absolute,
        equity_dd_maximal,
        equity_dd_maximal_percent,
    )

    curve = [120.0, 80.0, 150.0, 60.0, 90.0]
    assert equity_dd_maximal(curve, 100.0) == pytest.approx(90.0)
    assert equity_dd_absolute(curve, 100.0) == pytest.approx(40.0)
    assert equity_dd_maximal_percent(curve, 100.0) == pytest.approx(60.0)


def test_golden_recovery_factor_composes_mt5_net_and_equity_dd():
    # MT5 STAT_RECOVERY_FACTOR = -0.935547 = net / EquityDD_max。
    # 非トートロジー突合: 分子 net は MT5 約定列（_reconstruct_trades）から独立に算出し、
    # 分母 EquityDD_max は MT5 オラクル値（6594）を直接与える。recovery_factor_equity が
    # 「符号付き net / equity_dd_max」を正しく合成して MT5 recovery を再現することを固定する。
    # （equity-DD を engine 実走 equity_curve から得る非トートロジー突合は integration の
    #  test_ma_slope_reconcile.py が担う。本テストは式の合成正当性のみを担う。）
    from simulator.usecase.compute_stats import recovery_factor_equity, total_net_profit

    fx = _load_fixture()
    trades = _reconstruct_trades(fx["deals"])
    # MT5 results.equity_dd_max は "6 594 (63.28%)" 形式の文字列オラクル。金額部のみ抽出。
    mt5_equity_dd_max = float(
        fx["results"]["equity_dd_max"].split("(")[0].replace(" ", "")
    )  # = 6594.0（MT5 オラクル・逆算でない）
    # min(equity) = init - 安値 DD を満たす 2 点 curve（peak=init, trough=init-dd_max）。
    # ここで dd_max は MT5 オラクルを直接代入（curve から逆算した値ではない）。
    curve = [_MT5_INITIAL_DEPOSIT - mt5_equity_dd_max]
    rec = recovery_factor_equity(trades, curve, _MT5_INITIAL_DEPOSIT)
    # 自己整合: rec == net / dd_max（合成式の検証）。
    assert rec == pytest.approx(
        total_net_profit(trades) / mt5_equity_dd_max, abs=1e-12
    )
    # MT5 recovery オラクルと一致（net・dd_max とも MT5 由来の合成）。
    assert rec == pytest.approx(fx["results"]["recovery_factor_mt5"], abs=1e-4)


def test_recovery_factor_equity_zero_dd_returns_inf():
    # EquityDD_max == 0（単調増加 equity）で ∞ を返す（ゼロ除算回避）。
    import math

    from simulator.usecase.compute_stats import recovery_factor_equity

    trades = [
        TradeRecord(
            side="buy", volume=1.0, entry_time=0, exit_time=1,
            entry_price=1000.0, exit_price=1100.0,
            contract_size=1.0, swap=0.0, commission=0.0, exit_reason="tp",
        )
    ]
    assert recovery_factor_equity(trades, [10100.0], 10_000.0) == math.inf
