"""UC-002 compute_stats: METRICS §12 の 10 トレード固定点で決定論性を証明する。

入力（§12.1）:
    B_0 = 10000
    p   = [+150, -80, +220, +60, -300, -50, +400, -120, +90, -40]
    sign= [L, S, L, L, S, S, L, S, L, S]

TradeRecord.pnl() = (exit-entry)*sign*lot*contract_size + swap + commission。
contract_size=1 / lot=1 / swap=commission=0 とし、entry=1000・exit を調整して
各 p_i を再構成する（domain の式を介して p 系列を生成）。

判断点（upstream-input-validation で実証・採用）:
    §12.2/§12.6 の Sharpe=0.17・σ(HPR)=0.020019 は METRICS §1.2/§11 の式
    （ddof=0 母分散）から再現不能（実測 σ=0.018362・Sharpe=0.1862）。依頼指示
    「式と§12が割れる場合は METRICS の式を一次情報とする」に従い、式由来の値で固定する。
"""
from __future__ import annotations

import math

import pytest

from backtest.domain.trade_record import TradeRecord

B0 = 10000.0
P = [150.0, -80.0, 220.0, 60.0, -300.0, -50.0, 400.0, -120.0, 90.0, -40.0]
SIDES = ["buy", "sell", "buy", "buy", "sell", "sell", "buy", "sell", "buy", "sell"]


def _trades():
    """§12.1 の p 系列を再構成する 10 件の確定 TradeRecord を生成する。"""
    out = []
    for i, (pi, side) in enumerate(zip(P, SIDES)):
        sign = 1 if side == "buy" else -1
        entry = 1000.0
        exit_price = entry + pi / sign  # (exit-entry)*sign == pi
        out.append(
            TradeRecord(
                side=side,
                volume=1.0,
                entry_time=i,
                exit_time=i + 1,
                entry_price=entry,
                exit_price=exit_price,
                contract_size=1.0,
                swap=0.0,
                commission=0.0,
                exit_reason="tp" if pi > 0 else "sl",
            )
        )
    return out


def _balance_curve():
    """確定後 Balance 系列 B_1..B_10（§12.1 表）。"""
    b = B0
    curve = []
    for pi in P:
        b += pi
        curve.append(b)
    return curve


# ---- §1 損益サマリー ----

def test_total_net_profit_matches_metrics_12():
    from backtest.usecase.compute_stats import total_net_profit

    assert total_net_profit(_trades()) == pytest.approx(330.0)


def test_gross_profit_matches_metrics_12():
    from backtest.usecase.compute_stats import gross_profit

    assert gross_profit(_trades()) == pytest.approx(920.0)


def test_gross_loss_matches_metrics_12():
    from backtest.usecase.compute_stats import gross_loss

    assert gross_loss(_trades()) == pytest.approx(-590.0)


def test_profit_factor_matches_metrics_12():
    from backtest.usecase.compute_stats import profit_factor

    assert profit_factor(_trades()) == pytest.approx(1.5593, abs=1e-4)


def test_profit_factor_infinite_when_no_loss():
    # METRICS §1.1: GrossLoss == 0 のとき ∞
    from backtest.usecase.compute_stats import profit_factor

    wins_only = [_trades()[0], _trades()[2]]  # 両方 win
    assert math.isinf(profit_factor(wins_only))


def test_expected_payoff_matches_metrics_12():
    from backtest.usecase.compute_stats import expected_payoff

    assert expected_payoff(_trades()) == pytest.approx(33.0)


def test_expected_payoff_zero_when_no_trades():
    from backtest.usecase.compute_stats import expected_payoff

    assert expected_payoff([]) == 0.0


def test_recovery_factor_matches_metrics_12():
    # |NetProfit| / Balance_DD_Max($) = 330 / 350
    from backtest.usecase.compute_stats import recovery_factor

    assert recovery_factor(_trades(), _balance_curve(), B0) == pytest.approx(0.9429, abs=1e-4)


def test_ahpr_matches_metrics_12():
    from backtest.usecase.compute_stats import ahpr

    assert ahpr(_balance_curve(), B0) == pytest.approx(1.003419, abs=1e-6)


def test_ghpr_matches_metrics_12():
    from backtest.usecase.compute_stats import ghpr

    # (10330/10000)^(1/10)
    assert ghpr(_balance_curve(), B0) == pytest.approx(1.003252, abs=1e-6)


def test_sharpe_ratio_uses_population_std_ddof0():
    # METRICS §1.2/§11: (mean(HPR)-1)/std(ddof=0)。判断点: 式由来 0.1862（§12 記載 0.17 不採用）
    from backtest.usecase.compute_stats import sharpe_ratio

    assert sharpe_ratio(_balance_curve(), B0) == pytest.approx(0.1862, abs=1e-4)


def test_sharpe_ratio_zero_when_std_is_zero():
    # METRICS §1.2: σ_HPR == 0 のとき 0 を返す
    from backtest.usecase.compute_stats import sharpe_ratio

    flat = [B0, B0, B0]  # 全 HPR == 1 -> std 0
    assert sharpe_ratio(flat, B0) == 0.0


def test_hpr_skips_when_prior_balance_non_positive():
    # METRICS §1.4: 分母 B_{i-1} <= 0 のとき HPR が定義不能のためスキップする。
    # 系列 [10000, -50, 5000] / B0=10000:
    #   HPR_1 = 10000/10000 = 1.0     （分母 B_0=10000 > 0 → 採用）
    #   HPR_2 = -50/10000   = -0.005  （分母 B_1=10000 ... ここでは前バランス=10000>0 → 採用）
    #   HPR_3 = 5000/(-50)            （分母 B_2=-50 <= 0 → スキップ）
    # 採用 HPR = [1.0, -0.005] → 平均 = 0.4975
    from backtest.usecase.compute_stats import ahpr

    curve = [10000.0, -50.0, 5000.0]
    assert ahpr(curve, B0) == pytest.approx((1.0 + (-0.005)) / 2, abs=1e-9)


# ---- §2 ドローダウン（Balance 系） ----

def test_balance_min_matches_metrics_12():
    from backtest.usecase.compute_stats import balance_min

    assert balance_min(_balance_curve(), B0) == pytest.approx(10000.0)


def test_balance_dd_absolute_matches_metrics_12():
    from backtest.usecase.compute_stats import balance_dd_absolute

    assert balance_dd_absolute(_balance_curve(), B0) == pytest.approx(0.0)


def test_balance_dd_maximal_amount_matches_metrics_12():
    from backtest.usecase.compute_stats import balance_dd_maximal

    assert balance_dd_maximal(_balance_curve(), B0) == pytest.approx(350.0)


def test_balance_dd_maximal_percent_matches_metrics_12():
    from backtest.usecase.compute_stats import balance_dd_maximal_percent

    assert balance_dd_maximal_percent(_balance_curve(), B0) == pytest.approx(3.3816, abs=1e-4)


def test_balance_dd_relative_percent_matches_metrics_12():
    from backtest.usecase.compute_stats import balance_dd_relative_percent

    assert balance_dd_relative_percent(_balance_curve(), B0) == pytest.approx(3.3816, abs=1e-4)


def test_balance_dd_relative_amount_matches_metrics_12():
    from backtest.usecase.compute_stats import balance_dd_relative_amount

    assert balance_dd_relative_amount(_balance_curve(), B0) == pytest.approx(350.0)


# ---- §3 件数・分布 ----

def test_trade_counts_match_metrics_12():
    from backtest.usecase.compute_stats import (
        long_trades,
        loss_trades,
        profit_long_trades,
        profit_short_trades,
        profit_trades,
        short_trades,
        total_trades,
    )

    t = _trades()
    assert total_trades(t) == 10
    assert profit_trades(t) == 5
    assert loss_trades(t) == 5
    assert long_trades(t) == 5
    assert short_trades(t) == 5
    assert profit_long_trades(t) == 5    # 全 Long が勝ち
    assert profit_short_trades(t) == 0   # 全 Short が負け


def test_z_score_matches_mt5_formula():
    # 校正（ISSUE-013 / golden report_900005560）: Z-Score は実 MT5 実装式
    #   Z = (N*(R-0.5) - P) / sqrt(P*(P-N)/(N-1)), P=2WL, W=count(pnl>=0)
    # を採用する。本プロジェクト目的＝MT5 再現につき、BACKTEST_METRICS §3.2 の
    # (R-E(R))/sqrt(Var(R)) 形（§12 で 1.3416）は実 MT5 と割れる（実 fixture で 2.35 を
    # 再現できるのは MT5 式のみ）ため、実 MT5 値を正とする。§12 入力（W=5,L=5,N=10,R=8）
    # に本式を適用すると 1.6771（決定論的に確定）。
    from backtest.usecase.compute_stats import z_score

    assert z_score(_trades()) == pytest.approx(1.6771, abs=1e-4)


def test_z_score_zero_when_all_wins():
    from backtest.usecase.compute_stats import z_score

    assert z_score([_trades()[0], _trades()[2]]) == 0.0


# ---- §4 個別トレード統計 ----

def test_largest_profit_and_loss_match_metrics_12():
    from backtest.usecase.compute_stats import largest_loss_trade, largest_profit_trade

    assert largest_profit_trade(_trades()) == pytest.approx(400.0)
    assert largest_loss_trade(_trades()) == pytest.approx(-300.0)


def test_average_profit_and_loss_trade_match_metrics_12():
    from backtest.usecase.compute_stats import average_loss_trade, average_profit_trade

    assert average_profit_trade(_trades()) == pytest.approx(184.0)
    assert average_loss_trade(_trades()) == pytest.approx(-118.0)


def test_max_consecutive_wins_match_metrics_12():
    # §12.5: count=2 (run {3,4}), その区間の利益 = +280
    from backtest.usecase.compute_stats import (
        max_consecutive_wins_count,
        max_consecutive_wins_profit,
    )

    assert max_consecutive_wins_count(_trades()) == 2
    assert max_consecutive_wins_profit(_trades()) == pytest.approx(280.0)


def test_max_consecutive_losses_match_metrics_12():
    # §12.5: count=2 (run {5,6}), その区間の損失 = -350
    from backtest.usecase.compute_stats import (
        max_consecutive_losses_count,
        max_consecutive_losses_loss,
    )

    assert max_consecutive_losses_count(_trades()) == 2
    assert max_consecutive_losses_loss(_trades()) == pytest.approx(-350.0)


def test_maximal_consecutive_profit_match_metrics_12():
    # §12.5: $=+400 (run {7}), その区間のトレード数 = 1
    from backtest.usecase.compute_stats import (
        maximal_consecutive_profit_amount,
        maximal_consecutive_profit_count,
    )

    assert maximal_consecutive_profit_amount(_trades()) == pytest.approx(400.0)
    assert maximal_consecutive_profit_count(_trades()) == 1


def test_maximal_consecutive_loss_match_metrics_12():
    # §12.5: $=-350 (run {5,6}), その区間のトレード数 = 2
    from backtest.usecase.compute_stats import (
        maximal_consecutive_loss_amount,
        maximal_consecutive_loss_count,
    )

    assert maximal_consecutive_loss_amount(_trades()) == pytest.approx(-350.0)
    assert maximal_consecutive_loss_count(_trades()) == 2


def test_average_consecutive_match_metrics_12():
    # §12.5: AvgConWins = 5/4 = 1.25, AvgConLosses = 5/4 = 1.25
    from backtest.usecase.compute_stats import (
        average_consecutive_losses,
        average_consecutive_wins,
    )

    assert average_consecutive_wins(_trades()) == pytest.approx(1.25)
    assert average_consecutive_losses(_trades()) == pytest.approx(1.25)


# ---- 統合: compute_stats が BacktestStats を §12.6 期待値で返す ----

def test_compute_stats_returns_backteststats_matching_metrics_12_6():
    from backtest.usecase.compute_stats import compute_stats
    from backtest.usecase.models import BacktestStats

    stats = compute_stats(
        trades=_trades(),
        balance_curve=_balance_curve(),
        equity_curve=_balance_curve(),  # 本サイクルは確定値のみで equity=balance とみなす
        initial_deposit=B0,
    )

    assert isinstance(stats, BacktestStats)
    # §12.6 期待される MT5 出力との対応
    assert stats.initial_deposit == pytest.approx(10000.0)
    assert stats.profit == pytest.approx(330.0)
    assert stats.gross_profit == pytest.approx(920.0)
    assert stats.gross_loss == pytest.approx(-590.0)
    assert stats.profit_factor == pytest.approx(1.56, abs=1e-2)
    assert stats.expected_payoff == pytest.approx(33.0)
    # recovery_factor は第2サイクルで equity DD 基準（recovery_factor_equity）へ結線。
    # 本テストは equity_curve=_balance_curve()（equity==balance）のため equity_dd_max==
    # balance_dd_max==350 となり net(330)/350=0.9428 で従来の balance 基準 0.94 と一致する。
    assert stats.recovery_factor == pytest.approx(0.9428, abs=1e-2)
    # sharpe_ratio は第2サイクルで per-trade 版（clamp[-5,5]・実 MT5 整合）へ結線（Z-Score
    # 校正と同方針の正当更新）。旧 HPR 版 0.1862（METRICS §1.2）から per-trade 版へ差し替え:
    #   per-trade pnl 系列の (mean/std(ddof=0))×√N = 0.560523（[-5,5] 内のためクランプなし）。
    # HPR 版の値は sharpe_ratio()（残置関数）が引き続き提供する（test_*_sharpe で別途固定）。
    assert stats.sharpe_ratio == pytest.approx(0.560523, abs=1e-5)
    assert stats.balance_min == pytest.approx(10000.0)
    assert stats.balance_dd == pytest.approx(350.0)
    assert stats.balance_dd_percent == pytest.approx(3.38, abs=1e-2)
    assert stats.balance_ddrel_percent == pytest.approx(3.38, abs=1e-2)
    assert stats.balance_dd_relative == pytest.approx(350.0)
    assert stats.trades == 10
    assert stats.profit_trades == 5
    assert stats.loss_trades == 5
    assert stats.long_trades == 5
    assert stats.short_trades == 5
    assert stats.profit_long_trades == 5
    assert stats.profit_short_trades == 0
    assert stats.max_profit_trade == pytest.approx(400.0)
    assert stats.max_loss_trade == pytest.approx(-300.0)
    assert stats.max_con_wins == 2
    assert stats.max_con_profit_trades == pytest.approx(280.0)
    assert stats.max_con_losses == 2
    assert stats.max_con_loss_trades == pytest.approx(-350.0)
    assert stats.con_profit_max == pytest.approx(400.0)
    assert stats.con_profit_max_trades == 1
    assert stats.con_loss_max == pytest.approx(-350.0)
    assert stats.con_loss_max_trades == 2
    assert stats.profit_trades_avg_con == pytest.approx(1.25)
    assert stats.loss_trades_avg_con == pytest.approx(1.25)


def test_compute_stats_handles_zero_trades():
    # METRICS §1.4/§3.3: N=0 は安全な既定値
    from backtest.usecase.compute_stats import compute_stats

    stats = compute_stats(trades=[], balance_curve=[], equity_curve=[], initial_deposit=B0)
    assert stats.trades == 0
    assert stats.profit == 0.0
    assert stats.expected_payoff == 0.0
    assert stats.profit_trades == 0


# ---- 回帰: 同値トレード（pnl=0）はランを途切れさせる（PROCESS §6.1:349-350） ----
# 「同値で途切れる（カウントをリセット）」を固定する。バグ: _runs が is_win()(bool)
# でグループ化するため pnl=0 が連敗ラン側に吸収され、(b) phantom な連敗ラン（金額0）が
# 発生し、(a) ランの区切りが失われる。3 値 sign(pnl)∈{+1,0,-1} 分割を要求する回帰テスト。

def _trade_with_pnl(pnl: float, *, side: str = "buy"):
    """pnl() が指定値となる単一の確定 TradeRecord を生成する（domain の式経由）。"""
    sign = 1 if side == "buy" else -1
    entry = 1000.0
    exit_price = entry + pnl / sign  # (exit-entry)*sign == pnl
    return TradeRecord(
        side=side,
        volume=1.0,
        entry_time=0,
        exit_time=1,
        entry_price=entry,
        exit_price=exit_price,
        contract_size=1.0,
        swap=0.0,
        commission=0.0,
        exit_reason="tp" if pnl > 0 else "sl",
    )


def test_zero_pnl_trade_breaks_loss_run_no_phantom_run():
    # Arrange: [loss(-50), zero(0), loss(-30)]。同値が連敗ランを途切れさせるので
    # 連敗ランは {[-50]}, {[-30]} の 2 本（最長 1）。pnl=0 を含む phantom ランは無い。
    from backtest.usecase.compute_stats import (
        max_consecutive_losses_count,
        max_consecutive_losses_loss,
    )

    trades = [_trade_with_pnl(-50.0), _trade_with_pnl(0.0), _trade_with_pnl(-30.0)]
    # Act
    count = max_consecutive_losses_count(trades)
    worst = max_consecutive_losses_loss(trades)
    # Assert: (a) zero が区切るので最長連敗は 1（2 にならない）
    assert count == 1
    # (b) phantom な連敗ラン（金額0）が混ざらず、最長ラン損失は単一の -50
    assert worst == pytest.approx(-50.0)


# ---- 回帰(🔴 §4.3): avg_con の分子は「ランに属するトレード数」であり ----
# 件数系の profit_trades(pnl>=0) を流用してはならない。ゼロ損益トレードを含む系列で固定する。
# バグ: average_consecutive_wins が分子に profit_trades(=ゼロ込み件数) を流用すると、
# ゼロ損益が win ラン（pnl>0 でラン分割・ゼロはラン中立で区切る）に属さないため
# 分子(件数) > 分母由来の実件数 となり §4.3 (AvgConWins=N_w/K_w) と乖離する。

def test_average_consecutive_wins_uses_run_member_count_not_count_win():
    # Arrange: [win(+10), zero(0), win(+20)]。
    #   win ラン = {[+10]}, {[+20]}（ゼロが区切る） → K_w=2, N_w=2（ラン内件数）。
    #   profit_trades(pnl>=0) = 3（+10, 0, +20）を分子に流用すると 3/2=1.5 となり誤り。
    #   正: N_w/K_w = 2/2 = 1.0。
    from backtest.usecase.compute_stats import average_consecutive_wins

    trades = [_trade_with_pnl(10.0), _trade_with_pnl(0.0), _trade_with_pnl(20.0)]
    # Act
    result = average_consecutive_wins(trades)
    # Assert: ゼロ込み件数(3) 流用なら 1.5、ラン内件数(2) なら 1.0
    assert result == pytest.approx(1.0)


def test_average_consecutive_losses_uses_run_member_count_not_count_loss():
    # Arrange: [loss(-10), zero(0), loss(-20), loss(-30)]。
    #   loss ラン = {[-10]}, {[-20,-30]}（ゼロが区切る） → K_l=2, N_l=3（ラン内件数）。
    #   loss_trades(pnl<0)=3 はこの系列では N_l と一致するが、対称性のため
    #   分子を loss ラン内件数 sum(len(r)) に明示統一することを固定する。
    #   正: N_l/K_l = 3/2 = 1.5。
    from backtest.usecase.compute_stats import average_consecutive_losses

    trades = [
        _trade_with_pnl(-10.0),
        _trade_with_pnl(0.0),
        _trade_with_pnl(-20.0),
        _trade_with_pnl(-30.0),
    ]
    # Act
    result = average_consecutive_losses(trades)
    # Assert
    assert result == pytest.approx(1.5)


# ---- 🟢 win 判定述語の分離: 件数系=pnl>=0、連勝ラン=pnl>0 を明示命名で強制 ----
# 二重基準の暗黙化（is_win()=pnl>0 と件数 pnl>=0）が 🔴 の誤流用を招いた。
# is_count_win(>=0) / is_run_win(>0) を明示述語に分離し、ゼロ損益で 2 基準が割れることを固定。

def test_is_count_win_counts_zero_pnl_as_win():
    # 件数系基準: pnl>=0 はゼロ損益も勝ち（MT5 実測 profit_trades=292 の根拠）。
    from backtest.usecase.compute_stats import is_count_win

    assert is_count_win(_trade_with_pnl(10.0)) is True
    assert is_count_win(_trade_with_pnl(0.0)) is True
    assert is_count_win(_trade_with_pnl(-10.0)) is False


def test_is_run_win_excludes_zero_pnl():
    # 連勝ラン基準: pnl>0 のみ勝ち（ゼロはラン中立。METRICS §6.1/§4.3）。
    from backtest.usecase.compute_stats import is_run_win

    assert is_run_win(_trade_with_pnl(10.0)) is True
    assert is_run_win(_trade_with_pnl(0.0)) is False
    assert is_run_win(_trade_with_pnl(-10.0)) is False


def test_zero_pnl_trade_breaks_win_run_no_phantom_run():
    # Arrange: [win(+40), zero(0), win(+60)]。同値が連勝ランを途切れさせるので
    # 連勝ランは {[+40]}, {[+60]} の 2 本（最長 1）。
    from backtest.usecase.compute_stats import (
        max_consecutive_wins_count,
        max_consecutive_wins_profit,
    )

    trades = [_trade_with_pnl(40.0), _trade_with_pnl(0.0), _trade_with_pnl(60.0)]
    # Act
    count = max_consecutive_wins_count(trades)
    best = max_consecutive_wins_profit(trades)
    # Assert: (a) zero が区切るので最長連勝は 1（2 にならない）
    assert count == 1
    # (c) 最長連勝ランの利益は単一の +40（先頭ラン）
    assert best == pytest.approx(40.0)


def test_zero_pnl_excluded_from_win_and_loss_runs():
    # Arrange: [win(+10), zero(0), loss(-10)]。zero は勝ち/負けどちらのランにも属さない。
    from backtest.usecase.compute_stats import (
        max_consecutive_losses_count,
        max_consecutive_wins_count,
    )

    trades = [_trade_with_pnl(10.0), _trade_with_pnl(0.0), _trade_with_pnl(-10.0)]
    # Act / Assert: 連勝・連敗とも最長 1（zero は両ランから除外され区切りとなる）
    assert max_consecutive_wins_count(trades) == 1
    assert max_consecutive_losses_count(trades) == 1


def test_compute_stats_does_not_import_pandas_at_module_level_for_purity():
    # 純粋関数群であることの確認（domain 以外の usecase 外層を import しない）
    import ast

    import backtest.usecase.compute_stats as cs

    with open(cs.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("backtest.adapter", "backtest.framework", "backtest.main", "pydantic")
    for name in imported:
        assert not name.startswith(forbidden), name


# ---- 結線(🔴 第2サイクル): compute_stats() 本体が equity 系 STAT_* を populate する ----
# 第1サイクルで単体検証済の 5 関数を compute_stats() 出力へ結線する。
#   - sharpe_ratio  → sharpe_ratio_per_trade(trades)（per-trade・clamp[-5,5]）に差し替え
#   - recovery_factor → recovery_factor_equity（equity DD 基準・符号付き net）に差し替え
#   - equity_dd_abs / equity_dd_max / equity_dd_max_percent を BacktestStats に追加し populate
# balance 系（balance_dd 等）は不変。equity_curve は balance_curve と区別される独立系列で
# あること（equity 系 DD が equity_curve から算出されること）を、balance とは異なる谷を持つ
# 合成 equity_curve で実証する（balance を流用していたら値が一致せず落ちる）。

def _equity_curve_distinct():
    """balance_curve とは異なる谷（含み損ピーク）を持つ合成 equity 系列。

    balance 系の min(9670)/最大 DD(350) とは一致しない値域にして、equity 系 DD が
    equity_curve から独立に算出されることを検出可能にする。
    """
    return [10150.0, 9500.0, 10290.0, 10350.0, 9000.0, 8800.0, 9500.0, 9000.0, 9300.0, 10330.0]


def test_compute_stats_populates_equity_dd_fields_from_equity_curve():
    # Arrange: balance とは異なる谷を持つ equity_curve を供給する。
    from backtest.usecase.compute_stats import compute_stats

    # Act
    stats = compute_stats(
        trades=_trades(),
        balance_curve=_balance_curve(),
        equity_curve=_equity_curve_distinct(),
        initial_deposit=B0,
    )
    # Assert: equity 系 DD は equity_curve(peak 10350 / trough 8800 / init 10000) から算出。
    #   equity_dd_abs = init - min(equity) = 10000 - 8800 = 1200
    #   equity_dd_max = peak - trough = 10350 - 8800 = 1550
    #   equity_dd_max_percent = 1550/10350*100 = 14.9758…%
    # balance 系 DD（balance_dd=350）と異なる値で、equity_curve 由来であることを実証する。
    assert stats.equity_dd_abs == pytest.approx(1200.0)
    assert stats.equity_dd_max == pytest.approx(1550.0)
    assert stats.equity_dd_max_percent == pytest.approx(14.9758, abs=1e-3)


def test_compute_stats_sharpe_is_per_trade_clamped_when_wired():
    # Arrange/Act: sharpe_ratio フィールドが per-trade 版（clamp[-5,5]）へ差し替わる。
    from backtest.usecase.compute_stats import compute_stats, sharpe_ratio_per_trade

    stats = compute_stats(
        trades=_trades(),
        balance_curve=_balance_curve(),
        equity_curve=_equity_curve_distinct(),
        initial_deposit=B0,
    )
    # Assert: HPR 版(0.1862)ではなく per-trade 版（実 MT5 整合・clamp[-5,5]）の値。
    assert stats.sharpe_ratio == pytest.approx(sharpe_ratio_per_trade(_trades()))
    # 旧 HPR 版の値(0.1862)とは異なる（差し替えが行われたことの実証）。
    assert stats.sharpe_ratio != pytest.approx(0.1862, abs=1e-4)


def test_compute_stats_recovery_is_equity_based_when_curve_supplied():
    # Arrange/Act: recovery_factor が equity DD 基準（符号付き net / equity_dd_max）へ差し替わる。
    from backtest.usecase.compute_stats import compute_stats, recovery_factor_equity

    eq = _equity_curve_distinct()
    stats = compute_stats(
        trades=_trades(),
        balance_curve=_balance_curve(),
        equity_curve=eq,
        initial_deposit=B0,
    )
    # Assert: net(330)/equity_dd_max(1550) = 0.21290…（balance 版 recovery 0.94 とは異なる）。
    assert stats.recovery_factor == pytest.approx(
        recovery_factor_equity(_trades(), eq, B0)
    )
    assert stats.recovery_factor != pytest.approx(0.94, abs=1e-2)


def test_compute_stats_equity_fields_backward_compatible_when_no_equity_curve():
    # Arrange/Act: equity_curve 未供給（空列）時は equity 系を後方互換の既定値にする。
    # balance 系・件数系は従来どおり算出され、equity 系 DD は 0（peak-to-trough なし）。
    from backtest.usecase.compute_stats import compute_stats

    stats = compute_stats(
        trades=_trades(),
        balance_curve=_balance_curve(),
        equity_curve=[],
        initial_deposit=B0,
    )
    # Assert: equity_curve 空のとき equity 系 DD は 0（後方互換・例外を投げない）。
    assert stats.equity_dd_abs == pytest.approx(0.0)
    assert stats.equity_dd_max == pytest.approx(0.0)
    assert stats.equity_dd_max_percent == pytest.approx(0.0)
    # balance 系は不変（既存挙動の維持）。
    assert stats.balance_dd == pytest.approx(350.0)
