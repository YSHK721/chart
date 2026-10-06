"""EMA_Deviation_Short_EA の束縛（EMA 上方乖離の水準で売る EA・依頼 2026-10-06）。"""
from __future__ import annotations

from simulator.adapter.indicator import madiff as madiff_indicator
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.ema_deviation_short import EmaDeviationShort
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext, PlotDecl
from simulator.main.ea_bindings.sources import series_or_data_error, source_for


def build_registry(df, *, ma_period: int) -> PandasIndicatorRegistry:
    """終値の EMA（MaSlope と同じ MQL 忠実 EMA）と当該足の "open" を登録する。"""
    return PandasIndicatorRegistry(
        {
            "ema": madiff_indicator.ema_series(df["close"], ma_period),
            "open": series_or_data_error(df, "open"),
        }
    )


def _factory_ema_deviation_short(ctx: EaBuildContext):
    # frame と読み手は同じ 1 回の解決から受け取る（形式判定を 2 回発行しない）。
    source = source_for(ctx.data_path)
    registry = build_registry(source.frame, ma_period=ctx.param("ma_period"))
    return EmaDeviationShort(), registry, source.repository


BINDING = EaBinding(
    name="EMA_Deviation_Short_EA",
    strategy_type=EmaDeviationShort,
    build=_factory_ema_deviation_short,
    # EmaDeviationShort が読むもの: 水準の係数（期間・乖離率）、ロットの丸め、SL/TP の丸め。
    strategy_params=(
        "ma_period",
        "ema_deviation_pct",
        "lot_size",
        "volume_min",
        "volume_max",
        "volume_step",
        "stop_loss_points",
        "take_profit_points",
        "point_size",
        "digits",
        "stops_level",
    ),
    plots=(PlotDecl("ema", "price"),),
)
