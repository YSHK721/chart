"""サイジングを掛けた run でも待機注文の宣言が効く（ISSUE-557 のテストの穴）。

なぜ在るか: 合流点（「`build_interactor`」）が宣言を読むのは 「`strategy_decorator`」 を通った後の戦略である。
SizingDecorator が内側の宣言（「`pending_order_use`」）を名乗らないと、サイジングを掛けただけで
待機注文の宣言が消え、足の途中の評価点が無い run になる。

表明:
    1. 状態検証: SizingDecorator で包んだ MA_Slope_Pending_EA は、呼び出し側が何も渡さなくても、
       同じく包んだうえで MT5 照合済みの構成（`pending_lifecycle=True` を明示）と同じ取引になる
       （発注量まで一致）。宣言が透過しなければ、足の途中の評価点が無い run に待機注文が来て
       「`ConfigError`」 で止まる（ISSUE-557 原因の残りの是正）。成行の MA_Slope_EA との対照は置かない:
       サイジングは SL を必須とし、MA_Slope_EA は SL を受け付けないため同じ条件で走らない。
    2. 計算量: 包んだ run でも内側の宣言の読み取りは 1 run に 1 回（足の本数 2 点で変わらない）。
       観測点は宣言された注入点 「`strategy_override`」 と 「`strategy_decorator`」 だけ。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.adapter.strategy.sizing_decorator import build_sizing_decorator
from simulator.main import build_interactor
from simulator.usecase.pending_order_use import PendingOrderUse
from simulator.usecase.sizing_models import SizingConfig

_COMMON = dict(
    symbol="JP225", period="M1", initial_deposit=1_000_000.0, contract_size=1.0,
    volume_min=1.0, volume_max=100.0, volume_step=1.0, stops_level=0, digits=1,
    point_size=0.1, leverage=10.0, ma_period=5, ma_method="ema", lot_size=1.0,
    stop_loss_points=30, take_profit_points=30, slope_shift=1, slope_min_points=1.0,
    entry_offset_points=20.0, entry_type="limit",
)

_SIZING = SizingConfig(enabled=True, sims=50, seed=1)


def _decorator():
    """run_job の 「`_build_decorator`」 と同じ工場（量制約は属性で渡す）。"""
    return build_sizing_decorator(
        _SIZING,
        symbol_spec=SimpleNamespace(
            volume_min=_COMMON["volume_min"],
            volume_max=_COMMON["volume_max"],
            volume_step=_COMMON["volume_step"],
        ),
    )


def _write_csv(path: Path, bars: int) -> Path:
    """既存検定（test_pending_order_use_declared_by_strategy）と同じ形の足列。"""
    rows = ["date,open,high,low,close,volume,spread"]
    price = 20000.0
    for i in range(bars):
        step = 6.0 if (i // 7) % 2 == 0 else -6.0
        o = price
        c = price + step
        h = max(o, c) + (3.0 if i % 3 else 0.5)
        lo = min(o, c) - (3.0 if i % 2 else 0.5)
        rows.append(f"2024-01-02 {i // 60:02d}:{i % 60:02d}:00,{o},{h},{lo},{c},1,10")
        price = c
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _trades(ea_name: str, csv: Path, overrides: dict) -> list:
    controller, request = build_interactor(
        data_path=str(csv), ea_name=ea_name, config_overrides=overrides,
        strategy_decorator=_decorator(), **_COMMON,
    )
    result = controller.execute(request)
    return [
        (t.entry_time, t.side, t.entry_price, t.volume, t.exit_time, t.exit_price)
        for t in result.trades
    ]


def test_the_wrapped_declaring_strategy_runs_like_the_mt5_reconciled_configuration(tmp_path) -> None:
    csv = _write_csv(tmp_path / "md.csv", 240)
    base = {"tick_model": "every_tick", "stop_out_action": "close_and_halt"}

    declared = _trades("MA_Slope_Pending_EA", csv, dict(base))
    reconciled = _trades("MA_Slope_Pending_EA", csv, {**base, "pending_lifecycle": True})

    assert declared, "取引が 1 件も無いと比較が空振りする"
    assert declared == reconciled


class _CountingDeclaration:
    """宣言の読み取りを数える内側の戦略（Test Spy・待機注文は出さない）。"""

    entry_price_basis = "current_open"

    def __init__(self) -> None:
        self.reads = 0

    @property
    def pending_order_use(self) -> PendingOrderUse:
        self.reads += 1
        return PendingOrderUse(persistent=False, oco=False)

    def on_init(self, config, indicators) -> None:
        return None

    def on_new_bar(self, bar_index, indicators, account):
        return []

    def on_position_check(self, position, bar_index, indicators) -> str:
        return "hold"

    def on_tick(self, bar_index, bid, ask, account):
        return []


@pytest.mark.parametrize("bars", [60, 240])
def test_the_inner_declaration_is_read_once_per_wrapped_run(tmp_path, bars) -> None:
    csv = _write_csv(tmp_path / "md.csv", bars)
    spy = _CountingDeclaration()
    controller, request = build_interactor(
        data_path=str(csv), ea_name="MA_Slope_Pending_EA", strategy_override=spy,
        strategy_decorator=_decorator(), config_overrides={"tick_model": "every_tick"}, **_COMMON,
    )
    controller.execute(request)
    # 読み取り − 1 = 0（足の本数に依らない）
    assert spy.reads - 1 == 0
