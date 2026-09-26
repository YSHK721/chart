"""OpenThenClose5mLong_EA の束縛。"""
from __future__ import annotations

from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.open_then_close_5m import OpenThenClose5mLong
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import load_dataframe, ohlc_repository_for


def build_registry(df) -> PandasIndicatorRegistry:
    """終値のみを登録した IndicatorPort 実装を構築する。"""
    return PandasIndicatorRegistry({"close": df["close"].astype(float).reset_index(drop=True)})


def _factory_open_then_close_5m(ctx: EaBuildContext):
    df = load_dataframe(ctx.data_path)
    registry = build_registry(df)
    return OpenThenClose5mLong(), registry, ohlc_repository_for(ctx.data_path)


BINDING = EaBinding(
    name="OpenThenClose5mLong_EA",
    strategy_type=OpenThenClose5mLong,
    build=_factory_open_then_close_5m,
    strategy_params=("lot_size",),
)
