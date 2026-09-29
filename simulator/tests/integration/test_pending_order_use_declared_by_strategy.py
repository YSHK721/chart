"""待機注文の使い方は戦略が宣言する（ISSUE-557）。

なぜ在るか（実測 2026-09-29）: MA_Slope_Pending_EA は毎足 buy_limit / sell_limit を出す（1 か月で 472 / 471 件）が、
sim の Settings 経路は 「`pending_lifecycle`」 を渡さないため、エンジンが「すべて足境界の成行」として約定させた。
MA_Slope_EA（成行）と取引 943 件が約定時刻・価格まで一致した。MT5 と照合済みの構成
（`simulator/tests/confirmation/2026-03_ma-limit/reconcile.py`）は `pending_lifecycle=True` を呼び出し側が渡していた。

表明:
    1. 状態検証: 待機注文を宣言した戦略は、呼び出し側が何も渡さなくても、MT5 照合済みの構成
       （`pending_lifecycle=True` を明示）と同じ取引になる。成行の EA とは同じにならない。
    2. 宣言と食い違う値を呼び出し側が渡したら、実行前に ConfigError（黙ってどちらかを採らない）。
    3. 計算量: 宣言の読み取りは 1 run に 1 回（足の本数 2 点で変わらない）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.domain.exceptions import ConfigError
from simulator.main import build_interactor
from simulator.usecase.pending_order_use import PendingOrderUse

_COMMON = dict(
    symbol="JP225", period="M1", initial_deposit=1_000_000.0, contract_size=1.0,
    volume_min=1.0, volume_max=100.0, volume_step=1.0, stops_level=0, digits=1,
    point_size=0.1, leverage=10.0, ma_period=5, ma_method="ema", lot_size=1.0,
    stop_loss_points=0, take_profit_points=0, slope_shift=1, slope_min_points=1.0,
    entry_offset_points=20.0, entry_type="limit",
)


def _write_csv(path: Path, bars: int) -> Path:
    """気配幅つき marketdata 形式。上げ下げを繰り返し、指値に届く足と届かない足を作る。"""
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
        data_path=str(csv), ea_name=ea_name, config_overrides=overrides, **_COMMON
    )
    result = controller.execute(request)
    return [(t.entry_time, t.side, t.entry_price, t.exit_time, t.exit_price) for t in result.trades]


def test_the_declaring_strategy_runs_like_the_mt5_reconciled_configuration(tmp_path) -> None:
    csv = _write_csv(tmp_path / "md.csv", 240)
    base = {"tick_model": "every_tick", "stop_out_action": "close_and_halt"}

    declared = _trades("MA_Slope_Pending_EA", csv, dict(base))
    reconciled = _trades("MA_Slope_Pending_EA", csv, {**base, "pending_lifecycle": True})
    market = _trades("MA_Slope_EA", csv, dict(base))

    assert declared, "取引が 1 件も無いと比較が空振りする"
    assert declared == reconciled
    assert declared != market


def test_a_conflicting_caller_value_is_refused_before_running(tmp_path) -> None:
    csv = _write_csv(tmp_path / "md.csv", 60)
    with pytest.raises(ConfigError):
        build_interactor(
            data_path=str(csv), ea_name="MA_Slope_Pending_EA",
            config_overrides={"tick_model": "every_tick", "pending_persistent": True}, **_COMMON,
        )


class _CountingDeclaration:
    """宣言の読み取りを数える戦略（Test Spy・待機注文は出さない）。"""

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
def test_the_declaration_is_read_once_per_run(tmp_path, bars) -> None:
    csv = _write_csv(tmp_path / "md.csv", bars)
    spy = _CountingDeclaration()
    controller, request = build_interactor(
        data_path=str(csv), ea_name="MA_Slope_Pending_EA", strategy_override=spy,
        config_overrides={"tick_model": "every_tick"}, **_COMMON,
    )
    controller.execute(request)
    # 読み取り − 1 = 0（足の本数に依らない）
    assert spy.reads - 1 == 0
