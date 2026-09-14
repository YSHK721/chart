"""SimpleTouchLong_EA の束縛。"""
from __future__ import annotations

from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.simple_touch_long import SimpleTouchLong
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import load_dataframe, ohlc_repository_for


def build_registry(df) -> PandasIndicatorRegistry:
    """終値と SMA5 を登録した IndicatorPort 実装を構築する。"""
    sma_5 = df["close"].astype(float).rolling(window=5, min_periods=5).mean()
    return PandasIndicatorRegistry(
        {
            "close": df["close"].astype(float).reset_index(drop=True),
            "low": df["low"].astype(float).reset_index(drop=True),
            "sma_5": sma_5.reset_index(drop=True),
        }
    )


def _factory_simple_touch_long(ctx: EaBuildContext):
    df = load_dataframe(ctx.data_path)
    registry = build_registry(df)
    return SimpleTouchLong(), registry, ohlc_repository_for(ctx.data_path)


BINDING = EaBinding(
    name="SimpleTouchLong_EA",
    build=_factory_simple_touch_long,
    strategy_params=("lot_size",),
)
