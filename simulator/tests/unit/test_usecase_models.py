"""usecase/models.py のプレーン DTO 構造テスト（CLEAN_ARCH §9 / 依頼仕様）。

models は pydantic 非依存の dataclass / プレーン DTO。フィールド保持のみを検証する
（振る舞い・変換責務は持たない＝CLEAN_ARCH §8 違反②の解消）。
"""
from __future__ import annotations

import dataclasses

import pytest


# ---- BacktestConfig: PROCESS §7 決定論 9 項目を保持する ----

def test_backtest_config_holds_nine_determinism_policy_fields():
    # Arrange / Act
    from simulator.usecase.models import BacktestConfig

    cfg = BacktestConfig(
        tick_model="every_tick",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="broker",
        digits=5,
        legacy_quirks=False,
        return_basis="equity_simple_bar",
    )

    # Assert: PROCESS §7 の 9 項目が 1:1 で保持される
    assert cfg.tick_model == "every_tick"
    assert cfg.spread_model == "fixed"
    assert cfg.sltp_tie == "sl"           # 同足両ヒットは SL 優先（§7 #3）
    assert cfg.fill_delay == "next_tick"  # 発注足と同一ティック監視不可（§7 #4）
    assert cfg.ohlc_order == "auto"
    assert cfg.session_calendar == "broker"
    assert cfg.digits == 5
    assert cfg.legacy_quirks is False
    assert cfg.return_basis == "equity_simple_bar"


def test_symbol_spec_holds_order_validate_required_attributes():
    # Arrange / Act
    from simulator.usecase.models import SymbolSpec

    spec = SymbolSpec(
        contract_size=100_000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        digits=5,
        point_size=0.00001,
    )

    # Assert: domain.order.validate が duck typing で参照する 5 属性 + 依頼仕様の属性
    assert spec.volume_min == 0.01
    assert spec.volume_max == 100.0
    assert spec.volume_step == 0.01
    assert spec.stops_level == 10
    assert spec.point_size == 0.00001
    assert spec.contract_size == 100_000.0
    assert spec.digits == 5
    # `leverage` は口座属性であり SymbolSpec は持たない（ISSUE-445 段階 3-D2・設計書 §3.4）。
    # 「持たないこと」自体は
    # `simulator/tests/unit/test_symbol_spec_fields_are_symbol_sourced.py` が
    # 供給元の section から機械的に固定する（ここに名前を書いて二重化しない）。


def test_symbol_spec_satisfies_domain_order_validate_duck_typing():
    # SymbolSpec が domain.order.Order.validate の要求属性契約を満たすことを検証
    from simulator.domain.order import Order
    from simulator.usecase.models import SymbolSpec

    spec = SymbolSpec(
        contract_size=100_000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        digits=5,
        point_size=0.00001,
    )
    order = Order(side="buy", kind="market", volume=0.10, price=None)

    # Act / Assert: 適合する Order は例外を出さない（属性契約が成立している）
    assert order.validate(spec) is None


def test_backtest_stats_holds_stat_fields_one_to_one():
    from simulator.usecase.models import BacktestStats

    # Arrange / Act: METRICS STAT_* と 1:1 のフィールド + 計算値の保持のみ確認
    stats = BacktestStats(
        initial_deposit=10000.0,
        profit=330.0,
        gross_profit=920.0,
        gross_loss=-590.0,
        profit_factor=1.5593,
        recovery_factor=0.9429,
        expected_payoff=33.0,
        sharpe_ratio=0.1862,
        trades=10,
        profit_trades=5,
        loss_trades=5,
        long_trades=5,
        short_trades=5,
        profit_long_trades=5,
        profit_short_trades=0,
        balance_min=10000.0,
        balance_dd=350.0,
        balance_dd_percent=3.3816,
        balance_dd_relative=350.0,
        balance_ddrel_percent=3.3816,
        max_profit_trade=400.0,
        max_loss_trade=-300.0,
        max_con_wins=2,
        max_con_profit_trades=280.0,
        max_con_losses=2,
        max_con_loss_trades=-350.0,
        con_profit_max=400.0,
        con_profit_max_trades=1,
        con_loss_max=-350.0,
        con_loss_max_trades=2,
        profit_trades_avg_con=1.25,
        loss_trades_avg_con=1.25,
    )

    assert stats.profit == 330.0
    assert stats.gross_profit == 920.0
    assert stats.trades == 10
    assert stats.max_con_wins == 2
    assert stats.profit_trades_avg_con == 1.25


def test_backtest_result_holds_data_without_conversion_behavior():
    # CLEAN_ARCH §8 違反②: BacktestResult は to_html/to_markdown を持たずデータ保持のみ
    from simulator.usecase.models import BacktestResult, BacktestStats

    stats = BacktestStats(
        initial_deposit=10000.0, profit=0.0, gross_profit=0.0, gross_loss=0.0,
        profit_factor=0.0, recovery_factor=0.0, expected_payoff=0.0, sharpe_ratio=0.0,
        trades=0, profit_trades=0, loss_trades=0, long_trades=0, short_trades=0,
        profit_long_trades=0, profit_short_trades=0, balance_min=10000.0,
        balance_dd=0.0, balance_dd_percent=0.0, balance_dd_relative=0.0,
        balance_ddrel_percent=0.0, max_profit_trade=0.0, max_loss_trade=0.0,
        max_con_wins=0, max_con_profit_trades=0.0, max_con_losses=0,
        max_con_loss_trades=0.0, con_profit_max=0.0, con_profit_max_trades=0,
        con_loss_max=0.0, con_loss_max_trades=0, profit_trades_avg_con=0.0,
        loss_trades_avg_con=0.0,
    )
    result = BacktestResult(
        trades=["t"],
        deals=["d"],
        equity_curve=[10000.0],
        balance_curve=[10000.0],
        stats=stats,
        indicator_values={"MADiff": [0.0]},
    )

    # Assert: データ保持
    assert result.trades == ["t"]
    assert result.deals == ["d"]
    assert result.equity_curve == [10000.0]
    assert result.balance_curve == [10000.0]
    assert result.stats is stats
    assert result.indicator_values["MADiff"] == [0.0]
    # 変換責務を持たない（to_html / to_markdown / to_json / compare を持たない）
    assert not hasattr(result, "to_html")
    assert not hasattr(result, "to_markdown")
    assert not hasattr(result, "to_json")
    assert not hasattr(result, "compare")


def test_models_module_does_not_import_pydantic():
    # 依頼制約: usecase は pydantic を使わない（import 文の不在を AST で検査。
    # docstring 中の "pydantic 非依存" 等の言及は許容する）。
    import ast

    import simulator.usecase.models as m

    with open(m.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "pydantic" not in imported


def test_dtos_are_dataclasses():
    from simulator.usecase.models import (
        BacktestConfig,
        BacktestResult,
        BacktestStats,
        SymbolSpec,
    )

    assert dataclasses.is_dataclass(BacktestConfig)
    assert dataclasses.is_dataclass(SymbolSpec)
    assert dataclasses.is_dataclass(BacktestStats)
    assert dataclasses.is_dataclass(BacktestResult)
