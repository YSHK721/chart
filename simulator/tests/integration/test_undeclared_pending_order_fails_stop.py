"""待機注文の使い方を宣言しない戦略が待機注文を出したら止める（ISSUE-557 の原因の残り）。

なぜ在るか: 足の途中の評価点が無い run（「`pending_lifecycle`」 偽）では、エンジンは待機注文
（kind が market 以外）を「すべて足境界の成行」として約定させていた。宣言
（「`pending_order_use`」）を書き忘れた戦略は、指値と書いても黙って成行になり、書いた条件と違う
条件で走った結果が成功として出る。是正後は `ConfigError` で止める（理由に戦略名と注文の種類）。

表明:
    1. 状態検証: 宣言の無い戦略が待機注文を出すと `ConfigError`。理由に戦略名と注文の種類が載る。
    2. 対照: 同じ戦略でも呼び出し側が `pending_lifecycle=True` を渡した run は止まらない。
    3. 計算量: 止まった後に足を評価しない（「`on_new_bar`」 の呼出数 − 待機注文を出した呼出の序数 = 0）。
       足の本数を変えた 2 点で成立する（止める位置が入力の長さに依らない）。観測点は
       「`build_interactor`」 の宣言された注入点 「`strategy_override`」 だけ。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.domain.exceptions import ConfigError
from simulator.domain.order import Order
from simulator.main import build_interactor
from simulator.tests.ledger_stop_out import ledger_stop_out_level

_COMMON = dict(
    symbol="JP225", period="M1", initial_deposit=1_000_000.0, contract_size=1.0,
    volume_min=1.0, volume_max=100.0, volume_step=1.0, stops_level=0, digits=1,
    point_size=0.1, leverage=10.0, stop_out_level=ledger_stop_out_level(), ma_period=5, ma_method="ema", lot_size=1.0,
    stop_loss_points=0, take_profit_points=0, slope_shift=1, slope_min_points=1.0,
    entry_offset_points=20.0, entry_type="limit",
)

#: 待機注文を出す呼出の序数（何回目の 「`on_new_bar`」 で出すか）。
_EMIT_AT_CALL = 3


def _write_csv(path: Path, bars: int) -> Path:
    rows = ["date,open,high,low,close,volume,spread"]
    price = 20000.0
    for i in range(bars):
        step = 6.0 if (i // 7) % 2 == 0 else -6.0
        o = price
        c = price + step
        rows.append(f"2024-01-02 {i // 60:02d}:{i % 60:02d}:00,{o},{max(o, c) + 3},{min(o, c) - 3},{c},1,10")
        price = c
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class _UndeclaredLimitStrategy:
    """待機注文の使い方を宣言せずに buy_limit を出す戦略（Test Spy）。"""

    entry_price_basis = "current_open"

    def __init__(self) -> None:
        self.calls = 0
        self.emitted_at: "int | None" = None

    def on_init(self, config, indicators) -> None:
        return None

    def on_new_bar(self, bar_index, indicators, account):
        self.calls += 1
        if self.calls == _EMIT_AT_CALL:
            self.emitted_at = self.calls
            return [Order(side="buy", kind="buy_limit", volume=1.0, price=19000.0, sl=None, tp=None)]
        return []

    def on_position_check(self, position, bar_index, indicators) -> str:
        return "hold"

    def on_tick(self, bar_index, bid, ask, account):
        return []


def _run(csv: Path, strategy, overrides: dict):
    controller, request = build_interactor(
        data_path=str(csv), ea_name="MA_Slope_EA", strategy_override=strategy,
        config_overrides={"tick_model": "every_tick", **overrides}, **_COMMON,
    )
    return controller.execute(request)


def test_an_undeclared_pending_order_stops_the_run_with_the_reason(tmp_path) -> None:
    csv = _write_csv(tmp_path / "md.csv", 120)
    strategy = _UndeclaredLimitStrategy()

    with pytest.raises(ConfigError) as caught:
        _run(csv, strategy, {})

    message = str(caught.value)
    assert type(strategy).__name__ in message
    assert "buy_limit" in message


def test_the_same_strategy_runs_when_the_caller_turns_pending_lifecycle_on(tmp_path) -> None:
    csv = _write_csv(tmp_path / "md.csv", 120)
    strategy = _UndeclaredLimitStrategy()

    _run(csv, strategy, {"pending_lifecycle": True})

    # 待機注文を出した後も足の評価が続いた（止まっていない）
    assert strategy.calls > _EMIT_AT_CALL


@pytest.mark.parametrize("bars", [60, 240])
def test_no_bar_is_evaluated_after_the_stop(tmp_path, bars) -> None:
    csv = _write_csv(tmp_path / "md.csv", bars)
    strategy = _UndeclaredLimitStrategy()

    with pytest.raises(ConfigError):
        _run(csv, strategy, {})

    assert strategy.emitted_at is not None
    # 呼出数 − 待機注文を出した呼出の序数 = 0（止めた後に足を評価しない）
    assert strategy.calls - strategy.emitted_at == 0
