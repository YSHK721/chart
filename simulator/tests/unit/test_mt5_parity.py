"""参照仕様② mt5_parity.py（MT5 校正版）の代表値・モジュール直参照テスト。

ISSUE-094 🔵: compute_stats の参照仕様二重定義分離に伴い、MT5 golden 校正版の統計式が
mt5_parity.py に単独モジュールとして存在し、代表入力で MT5 校正どおりの値を返すことを
モジュール直参照（compute_stats 経由でなく）で固定する。件数規則 pnl>=0・クランプ
Sharpe・equity 符号付き recovery・equity DD・MT5 Z-Score の代表挙動を監視する。
"""
from __future__ import annotations

import math

import pytest

from simulator.domain.trade_record import TradeRecord
from simulator.usecase import mt5_parity


def _trade(pnl: float, side: str = "buy"):
    sign = 1 if side == "buy" else -1
    entry = 1000.0
    return TradeRecord(
        side=side, volume=1.0, entry_time=0, exit_time=1,
        entry_price=entry, exit_price=entry + pnl / sign,
        contract_size=1.0, swap=0.0, commission=0.0,
        exit_reason="tp" if pnl > 0 else "sl",
    )


# ---- 件数規則: pnl>=0 を勝ちに数える（METRICS の pnl>0 と別ルール）----

def test_is_count_win_counts_zero_as_win():
    assert mt5_parity.is_count_win(_trade(0.0)) is True
    assert mt5_parity.is_count_win(_trade(-1.0)) is False


def test_profit_trades_includes_zero_pnl():
    # pnl>=0 を勝ちに数える: [+10, 0, -5] → profit_trades=2, loss_trades=1
    trades = [_trade(10.0), _trade(0.0), _trade(-5.0)]
    assert mt5_parity.profit_trades(trades) == 2
    assert mt5_parity.loss_trades(trades) == 1


def test_profit_long_short_split():
    trades = [_trade(10.0, "buy"), _trade(-5.0, "buy"),
              _trade(20.0, "sell"), _trade(0.0, "sell")]
    assert mt5_parity.profit_long_trades(trades) == 1   # buy +10
    assert mt5_parity.profit_short_trades(trades) == 2  # sell +20, sell 0


# ---- 平均（MT5 校正: 分母は pnl>=0 / pnl<0 件数）----

def test_average_profit_trade_divides_by_pnl_ge_zero_count():
    # gross_profit=10, profit_trades(pnl>=0)=2（+10 と 0）→ 10/2 = 5
    trades = [_trade(10.0), _trade(0.0), _trade(-4.0)]
    assert mt5_parity.average_profit_trade(trades) == pytest.approx(5.0)


def test_average_loss_trade():
    trades = [_trade(10.0), _trade(-4.0), _trade(-6.0)]
    assert mt5_parity.average_loss_trade(trades) == pytest.approx(-5.0)


# ---- Sharpe（per-trade・[-5,5] クランプ）----

def test_sharpe_per_trade_clamped_to_minus5():
    # 全負・低分散気味 → 素値が -5 を下回りクランプで -5.0（境界）
    trades = [_trade(-100.0), _trade(-101.0), _trade(-100.0), _trade(-101.0)]
    assert mt5_parity.sharpe_ratio_per_trade(trades) == pytest.approx(-5.0)


def test_sharpe_per_trade_within_band_not_clamped():
    # 素値が [-5,5] 内なら非クランプでそのまま返す（中間値ケース）
    trades = [_trade(10.0), _trade(-10.0), _trade(20.0), _trade(-5.0)]
    raw = mt5_parity.sharpe_ratio_per_trade(trades)
    assert -5.0 < raw < 5.0


def test_sharpe_per_trade_zero_when_uniform():
    # σ==0（全トレード同額）→ 0.0
    assert mt5_parity.sharpe_ratio_per_trade([_trade(10.0), _trade(10.0)]) == 0.0


# ---- Recovery（equity DD 基準・符号付き net）----

def test_recovery_factor_equity_signed_net():
    # net = -50 + -50 = -100（負）。equity DD 基準・符号付き → 負値になる。
    trades = [_trade(-50.0), _trade(-50.0)]
    equity = [9950.0, 9900.0]  # 単調減少 → equity_dd_max = 100
    val = mt5_parity.recovery_factor_equity(trades, equity, 10000.0)
    assert val == pytest.approx(-100.0 / 100.0)  # -1.0（符号付き）


# ---- equity ドローダウン ----

def test_equity_dd_maximal_peak_to_trough():
    equity = [10420.0, 10000.0, 3826.0, 5000.0]  # peak 10420 → trough 3826
    assert mt5_parity.equity_dd_maximal(equity, 10000.0) == pytest.approx(6594.0)


# ---- Z-Score（Wald-Wolfowitz・pnl>=0/<0 の 2 値ラン）----

def test_z_score_zero_when_single_class():
    # 全勝 → L=0 → 0.0
    assert mt5_parity.z_score([_trade(10.0), _trade(20.0)]) == 0.0


def test_z_score_nonzero_for_mixed():
    trades = [_trade(10.0), _trade(-5.0), _trade(20.0), _trade(-3.0)]
    assert mt5_parity.z_score(trades) != 0.0
