"""参照仕様① metrics_spec.py（METRICS 文書版）の代表値・モジュール直参照テスト。

ISSUE-094 🔵: compute_stats の参照仕様二重定義分離に伴い、METRICS 文書版の統計式が
metrics_spec.py に単独モジュールとして存在し、代表入力で文書仕様どおりの値を返すことを
モジュール直参照（compute_stats 経由でなく）で固定する。数値は既存 golden/§12 と整合。
"""
from __future__ import annotations

import math

import pytest

from simulator.domain.trade_record import TradeRecord
from simulator.usecase import metrics_spec

# METRICS §12.1 固定点（10 トレード）。
P = [150.0, -80.0, 220.0, 60.0, -300.0, -50.0, 400.0, -120.0, 90.0, -40.0]
SIDES = ["buy", "sell", "buy", "buy", "sell", "sell", "buy", "sell", "buy", "sell"]
B0 = 10000.0


def _trades():
    out = []
    for i, (pi, side) in enumerate(zip(P, SIDES)):
        sign = 1 if side == "buy" else -1
        entry = 1000.0
        out.append(TradeRecord(
            side=side, volume=1.0, entry_time=i, exit_time=i + 1,
            entry_price=entry, exit_price=entry + pi / sign,
            contract_size=1.0, swap=0.0, commission=0.0,
            exit_reason="tp" if pi > 0 else "sl",
        ))
    return out


def _balance_curve():
    b = B0
    curve = []
    for pi in P:
        b += pi
        curve.append(b)
    return curve


# ---- §1 損益サマリー（文書版） ----

def test_total_net_profit():
    assert metrics_spec.total_net_profit(_trades()) == pytest.approx(330.0)


def test_gross_profit_and_loss():
    assert metrics_spec.gross_profit(_trades()) == pytest.approx(920.0)
    assert metrics_spec.gross_loss(_trades()) == pytest.approx(-590.0)


def test_profit_factor():
    assert metrics_spec.profit_factor(_trades()) == pytest.approx(1.5593, abs=1e-4)


def test_profit_factor_infinite_when_no_loss():
    # METRICS §1.1: GrossLoss == 0 のとき ∞
    assert math.isinf(metrics_spec.profit_factor([_trades()[0], _trades()[2]]))


def test_expected_payoff():
    assert metrics_spec.expected_payoff(_trades()) == pytest.approx(33.0)


# ---- §1.2 Sharpe（HPR 文書版・ddof=0）---

def test_sharpe_ratio_hpr_doc_version():
    # METRICS §1.2: (mean(HPR)-1)/std(HPR, ddof=0)。golden §12 実測 0.1862。
    val = metrics_spec.sharpe_ratio(_balance_curve(), B0)
    assert val == pytest.approx(0.1862, abs=1e-3)


def test_sharpe_ratio_zero_when_empty():
    assert metrics_spec.sharpe_ratio([], B0) == 0.0


# ---- §1.1 recovery（balance 基準・文書版）---

def test_recovery_factor_balance_basis():
    # |net| / balance_dd_max。net=330。balance_dd_max は §12 曲線から算出。
    trades = _trades()
    curve = _balance_curve()
    dd = metrics_spec.balance_dd_maximal(curve, B0)
    expected = abs(metrics_spec.total_net_profit(trades)) / dd
    assert metrics_spec.recovery_factor(trades, curve, B0) == pytest.approx(expected)


def test_recovery_factor_infinite_when_no_dd():
    # 単調増加 balance（DD=0）→ ∞
    curve = [B0 + 100.0, B0 + 200.0]
    assert math.isinf(metrics_spec.recovery_factor(_trades()[:1], curve, B0))


# ---- §2 balance ドローダウン（文書版）---

def test_balance_dd_absolute_and_maximal():
    # §12.1 曲線: peak=10350（+150-80+220+60 後）→ その後 -300,-50 で 10000 まで下落。
    # 最大金額 DD は peak 10350 → trough 10000 の 350。
    curve = _balance_curve()
    assert metrics_spec.balance_dd_maximal(curve, B0) == pytest.approx(350.0)


# ---- §4 連勝/連敗ラン（is_run_win=pnl>0・文書版）---

def test_is_run_win_excludes_zero():
    z = TradeRecord(side="buy", volume=1.0, entry_time=0, exit_time=1,
                    entry_price=1000.0, exit_price=1000.0, contract_size=1.0,
                    swap=0.0, commission=0.0, exit_reason="tp")
    assert metrics_spec.is_run_win(z) is False  # pnl==0 は勝ちに数えない


def test_average_consecutive_wins_metrics_12():
    # §12.5: AvgConWins = 5/4 = 1.25
    assert metrics_spec.average_consecutive_wins(_trades()) == pytest.approx(1.25)


def test_max_consecutive_wins_count_and_profit():
    assert metrics_spec.max_consecutive_wins_count(_trades()) == 2
    assert metrics_spec.max_consecutive_wins_profit(_trades()) == pytest.approx(280.0)
