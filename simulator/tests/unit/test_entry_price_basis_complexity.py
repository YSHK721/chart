"""判定の瞬間の宣言の**計算量**（絶対命令 2026-08-28・ISSUE-533 段階 1）。

状態検証（約定価格が正しいか）では原理的に落ちない欠陥がある: 宣言は run のあいだ不変な
1 つの値なのに、発注ごと・評価点ごとに導き直しても**出力は正しいまま**である。したがって
測るのは時間ではなく**回数**であり、固定するのは回数そのものではなく**無駄の不在**である。

固定する不変条件:
    1. 発行 − 使用 = 0: 足境界の約定クォート（`derive_quotes`）は、実際に成行を約定させた
       足の数だけ発行される（引いて捨てる足が無い）。
    2. オーダーの表明: 規模（足数・発注数）を 3 倍にしても、宣言を導く回数は増えない。
       回数の期待値は焼き込まない——「N 回呼ばれること」を固定すると浪費が仕様に昇格する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from simulator.adapter.strategy.generic_condition_strategy import GenericConditionStrategy
from simulator.domain.entry_conditions import Condition, EntryConditions
from simulator.main import build_interactor
from simulator.usecase import order_execution

#: 2024-01-01T00:00:00Z（comma 形式 CSV の 「`time`」 は epoch 秒 int が契約）。
_EPOCH_2024_01_01 = 1_704_067_200
#: 規模の 2 点（片方が他方の 3 倍）。
_SMALL_BARS = 8
_LARGE_BARS = 24


class _CountingGeneric(GenericConditionStrategy):
    """宣言を読まれた回数を数える Test Spy（振る舞いは素の戦略と同一）。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.declaration_reads = 0
        self.bars_with_orders = 0

    @property
    def entry_price_basis(self) -> "str | None":
        self.declaration_reads += 1
        return GenericConditionStrategy.entry_price_basis.fget(self)

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> list:
        orders = super().on_new_bar(bar_index, indicators, account)
        self.bars_with_orders += 1 if orders else 0
        return orders


#: 条件の閾値。合成バーの終値はこの値を途中でまたぐので、**発注が出ない足が必ず残る**。
#: これが無いと「注文の無い足でクォートを引く」浪費を検定が 1 ビットも検出しない
#: （実測 2026-09-25: 毎足発注する素材では変異 M9 が 86 passed のまま素通りした）。
_THRESHOLD = 1.1005


def _fires_above_the_threshold() -> "_CountingGeneric":
    """終値が閾値を超えた足だけ発注する戦略（保有側が塞がるので買い・売りが交互に出る）。"""
    condition = [Condition(indicator="close", shift=0, op=">", rhs=_THRESHOLD)]
    return _CountingGeneric(
        entry_long=EntryConditions(list(condition)),
        entry_short=EntryConditions(list(condition)),
    )


def _write_csv(path: Path, bar_count: int) -> Path:
    rows = []
    for i in range(bar_count):
        base = 1.1000 + i * 0.0001
        rows.append(
            {
                "time": _EPOCH_2024_01_01 + 60 * i,
                "open": base,
                "high": base + 0.0005,
                "low": base - 0.0005,
                "close": base + 0.0002,
                "volume": 100,
                "spread": 0,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


class _QuoteCounter:
    """`derive_quotes` の発行回数を数える Test Spy（戻り値は素の実装のまま）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._inner(*args, **kwargs)


def _measure(tmp_path: Path, monkeypatch: Any, bar_count: int) -> "tuple[int, int, int]":
    """1 run 分の (宣言の導出回数, クォート発行回数, 成行を出した足数) を測る。

    素材は**発注の出ない足を必ず含む**（`_THRESHOLD`）。発行と使用が一致することは、
    注文の無い足でクォートを引いていないことと同義である。
    """
    csv_path = _write_csv(tmp_path / f"m1_{bar_count}.csv", bar_count)
    strategy = _fires_above_the_threshold()
    counter = _QuoteCounter(order_execution.derive_quotes)
    monkeypatch.setattr(order_execution, "derive_quotes", counter)
    controller, request = build_interactor(
        data_path=csv_path,
        symbol="SYNTH",
        period="M1",
        ea_name="TC24051901",
        initial_deposit=1_000_000.0,
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=0.0001,
        leverage=100.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=0,
        take_profit_points=0,
        strategy_override=strategy,
        config_overrides={"tick_model": "open_only"},
    )
    controller.execute(request)
    return strategy.declaration_reads, counter.calls, strategy.bars_with_orders


def test_quotes_are_issued_only_for_the_bars_that_actually_fill(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """発行 − 使用 = 0（引いて捨てる足が無い）。"""
    # Arrange / Act
    _, small_quotes, small_fills = _measure(tmp_path, monkeypatch, _SMALL_BARS)
    _, large_quotes, large_fills = _measure(tmp_path, monkeypatch, _LARGE_BARS)
    # Assert
    assert small_fills > 0 and large_fills > small_fills, "規模差が出ていない（検定が空虚）"
    assert small_quotes - small_fills == 0
    assert large_quotes - large_fills == 0


def test_the_declaration_is_not_derived_again_as_the_run_grows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """規模を 3 倍にしても宣言を導く回数は増えない（回数そのものは固定しない）。"""
    # Arrange / Act
    small_reads, _, small_fills = _measure(tmp_path, monkeypatch, _SMALL_BARS)
    large_reads, _, large_fills = _measure(tmp_path, monkeypatch, _LARGE_BARS)
    # Assert
    assert large_fills > small_fills, "規模差が出ていない（検定が空虚）"
    assert small_reads == large_reads
