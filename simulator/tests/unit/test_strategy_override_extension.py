"""build_interactor の `strategy_override` 拡張点の単体検定（Phase 6・注入方式＝専用 param 新設）.

依頼者承認済み改変（指示書 §「依頼者承認済み」）:
    `build_interactor` へ **keyword-only 任意引数 `strategy_override`（既定 None）** を追加する。
    None のとき既存挙動と byte 等価（MT5 突合の回帰ゼロ）。指定時は _EA_FACTORIES 構築の
    戦略を置き換え、strategy_decorator（sizing）は override された戦略へ適用する。

固定する不変条件:
    1. 既定 None → 素通り（_EA_FACTORIES が返した戦略がそのまま渡る＝byte 等価）。
    2. override 指定 → override インスタンスが Interactor へ渡る。
    3. override × strategy_decorator → sizing wrap は override へ適用される（合成順の両立）。
    4. 指標レジストリ・market_data・tick_model の選択は override の有無で変わらない。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from simulator.main import build_interactor
from simulator.usecase.ports import StrategyPort


def _write_csv(path: Path) -> Path:
    rows = []
    for i in range(12):
        base = 1.1000 + i * 0.0001
        rows.append(
            {
                "time": f"2024-01-0{1 + i // 6} {i % 6:02d}:00:00",
                "open": base,
                "high": base + 0.0005,
                "low": base - 0.0005,
                "close": base + 0.0002,
                "volume": 100,
                "spread": 10,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _meta(csv_path: Path, **overrides) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="EURUSD",
        period="M1",
        ea_name="TC24051901",
        initial_deposit=10_000.0,
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
        stop_loss_points=500,
        take_profit_points=3000,
    )
    base.update(overrides)
    return base


class _Override(StrategyPort):
    """置換に使う最小 StrategyPort。"""

    def on_init(self, config: Any, indicators: Any) -> None:
        pass

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any):
        return []

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"


class _Wrapper(StrategyPort):
    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def on_init(self, config: Any, indicators: Any) -> None:
        self.inner.on_init(config, indicators)

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any):
        return self.inner.on_new_bar(bar_index, indicators, account)

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return self.inner.on_position_check(position, bar_index, indicators)


def test_default_none_passes_factory_strategy_through(tmp_path: Path) -> None:
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    controller, _ = build_interactor(**_meta(csv_path))
    # Assert: 既定は従来どおり TC 経路（byte 等価）
    from simulator.adapter.strategy.tc24051901 import TC24051901

    assert isinstance(controller._interactor._strategy, TC24051901)


def test_explicit_none_is_byte_equivalent(tmp_path: Path) -> None:
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    controller, _ = build_interactor(**_meta(csv_path, strategy_override=None))
    # Assert
    from simulator.adapter.strategy.tc24051901 import TC24051901

    assert isinstance(controller._interactor._strategy, TC24051901)


def test_override_replaces_factory_strategy(tmp_path: Path) -> None:
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    override = _Override()
    # Act
    controller, _ = build_interactor(**_meta(csv_path, strategy_override=override))
    # Assert: Interactor へ渡るのは override インスタンスそのもの
    assert controller._interactor._strategy is override


def test_override_is_wrapped_by_strategy_decorator(tmp_path: Path) -> None:
    # Arrange: override × sizing decorator → sizing は override を包む（合成順の両立）
    csv_path = _write_csv(tmp_path / "m1.csv")
    override = _Override()
    # Act
    controller, _ = build_interactor(
        **_meta(csv_path, strategy_override=override, strategy_decorator=_Wrapper)
    )
    # Assert: 最外は Wrapper・内側は override（ファクトリ戦略ではない）
    strategy = controller._interactor._strategy
    assert isinstance(strategy, _Wrapper)
    assert strategy.inner is override


def test_override_does_not_change_other_wiring(tmp_path: Path) -> None:
    # Arrange
    csv_path = _write_csv(tmp_path / "m1.csv")
    # Act
    plain, plain_req = build_interactor(**_meta(csv_path))
    overridden, over_req = build_interactor(
        **_meta(csv_path, strategy_override=_Override())
    )
    # Assert: 指標・market_data・tick_model・symbol_spec は override と独立
    assert type(plain._interactor._tick_model) is type(overridden._interactor._tick_model)
    assert type(plain._interactor._indicators) is type(overridden._interactor._indicators)
    assert type(plain._market_data) is type(overridden._market_data)
    assert plain_req.symbol_spec == over_req.symbol_spec
