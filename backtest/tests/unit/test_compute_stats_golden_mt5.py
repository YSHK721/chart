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

import json
from pathlib import Path

import pytest

from backtest.domain.trade_record import TradeRecord
from backtest.usecase.compute_stats import compute_stats

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "mt5_outputs"
    / "report_900005560.json"
)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


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
