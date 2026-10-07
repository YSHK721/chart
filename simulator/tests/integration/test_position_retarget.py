"""保有中の玉の SL/TP を新しい足で動かす口（PositionRetargetPort・2026-10-07・依頼者承認）。

表明:
    1. 状態検証: 口が返した TP が玉に反映され、その価格で決済される（次の評価点から効く）。
    2. 計算量: 呼び出し − 「保有していた判定足の数」 = 0。足の数（2 点）と選んだ足（M1/Daily）を
       変えても成り立つ。期待値は入力の宣言（建てた足・足の数）から導く。
    観測の境界は宣言された差し込み口 `strategy_override`（Test Spy を渡す）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.domain.order import Order
from simulator.main import build_interactor
from simulator.tests.ledger_stop_out import ledger_stop_out_level

_DAY0 = 1_704_153_600  # 2024-01-02T00:00:00Z
_COMMON = dict(
    symbol="JP225", initial_deposit=10_000_000.0, contract_size=1.0,
    volume_min=1.0, volume_max=100.0, volume_step=1.0, stops_level=0, digits=1,
    point_size=0.1, leverage=100.0, stop_out_level=ledger_stop_out_level(),
    ma_period=21, ma_method="ema", lot_size=1.0,
    stop_loss_points=0, take_profit_points=0,
)


def _write(path: Path, days: int, per_day: int, low_at: "tuple[int, int] | None" = None) -> Path:
    import datetime as _dt

    rows = ["date,open,high,low,close,volume,spread"]
    for d in range(days):
        for m in range(per_day):
            t = _dt.datetime.fromtimestamp(_DAY0 + d * 86_400 + m * 60, _dt.timezone.utc)
            low = 19_000.0 if low_at == (d, m) else 19_995.0
            rows.append(f"{t:%Y-%m-%d %H:%M:%S},20000.0,20005.0,{low},20000.0,1,10")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class _RetargetSpy:
    """判定足 1 で成行売りを 1 回だけ出し、保有中は TP を ``tp`` へ動かす戦略（Test Spy）。"""

    entry_price_basis = "current_open"

    def __init__(self, tp: "float | None") -> None:
        self.tp = tp
        self.calls: "list[int]" = []

    def on_init(self, config, indicators) -> None:
        return None

    def on_new_bar(self, bar_index, indicators, account):
        if bar_index == 1:
            return [Order(side="sell", kind="market", volume=1.0, price=None, sl=None, tp=None)]
        return []

    def on_position_check(self, position, bar_index, indicators) -> str:
        return "hold"

    def retarget_positions(self, bar_index, indicators, position):
        self.calls.append(bar_index)
        return None, self.tp


def _run(csv: Path, period: str, spy) -> "tuple[list, int]":
    kw = {} if period == "M1" else {"data_period": "M1"}
    controller, request = build_interactor(
        data_path=str(csv), ea_name="MA_Slope_EA", period=period, strategy_override=spy,
        config_overrides={"tick_model": "ohlc_expand", "stop_out_action": "close_and_halt"},
        **kw, **_COMMON,
    )
    trades = controller.execute(request).trades
    ds = request.decision_series
    decisions = len(request.bars) if ds is None else len(ds.bars)
    return trades, decisions


def test_the_returned_tp_is_applied_and_the_position_closes_there(tmp_path: Path) -> None:
    csv = _write(tmp_path / "md.csv", days=1, per_day=10, low_at=(0, 5))
    spy = _RetargetSpy(tp=19_500.0)
    trades, _ = _run(csv, "M1", spy)
    assert (trades[0].exit_reason, trades[0].exit_price) == ("tp", 19_500.0)


@pytest.mark.parametrize(
    ("period", "days", "per_day"), [("M1", 1, 10), ("M1", 1, 40), ("Daily", 6, 20), ("Daily", 12, 20)]
)
def test_calls_minus_held_decision_bars_is_zero(tmp_path, period, days, per_day) -> None:
    # Arrange: TP に届かない（None＝変えない）ので、判定足 1 で建てた玉は期間の終わりまで残る。
    csv = _write(tmp_path / "md.csv", days=days, per_day=per_day)
    spy = _RetargetSpy(tp=None)
    # Act
    _trades, decisions = _run(csv, period, spy)
    # Assert: 保有していた判定足 = 建てた足（1）より後の判定足 = decisions − 2。
    held = decisions - 2
    assert held > 0, "保有した判定足が無い（検定が空虚）"
    assert len(spy.calls) - held == 0, spy.calls
    assert spy.calls == list(range(2, decisions))
