"""EMA_Deviation_Short_EA の束縛（EMA 上方乖離の水準で売る EA・依頼 2026-10-06）。"""
from __future__ import annotations

import pandas as pd

from simulator.adapter.indicator import madiff as madiff_indicator
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.ema_deviation_short import (
    LEVEL_SERIES,
    THRESHOLD_SERIES,
    EmaDeviationShort,
    limit_prices,
    threshold_series,
)
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext, PlotDecl
from simulator.main.ea_bindings.sources import series_or_data_error, source_for


def build_registry(
    df, *, ma_period: int, deviation_pct: float, point_size: float, digits: int
) -> PandasIndicatorRegistry:
    """終値の EMA（MaSlope と同じ MQL 忠実 EMA）・足ごとの乖離の水準・当該足の "open" を登録する。"""
    ema = madiff_indicator.ema_series(df["close"], ma_period)
    threshold = threshold_series(ema, ma_period=ma_period, deviation_pct=deviation_pct)
    return PandasIndicatorRegistry(
        {
            "ema": ema,
            THRESHOLD_SERIES: threshold,
            # 足 k の指値価格（チャートが描く）。戦略が読む系列を 1 本ずらし、戦略と同じ関数で
            #   刻みへ切り上げる（指値と線の値は必ず一致する）。
            LEVEL_SERIES: pd.Series(
                limit_prices(threshold.shift(1), point_size=point_size, digits=digits),
                index=threshold.index,
            ),
            "open": series_or_data_error(df, "open"),
        }
    )


def _factory_ema_deviation_short(ctx: EaBuildContext):
    # frame と読み手は同じ 1 回の解決から受け取る（形式判定を 2 回発行しない）。
    source = source_for(ctx.data_path)
    registry = build_registry(
        source.frame,
        ma_period=ctx.param("ma_period"),
        deviation_pct=ctx.param("ema_deviation_pct"),
        point_size=ctx.param("point_size"),
        digits=ctx.param("digits"),
    )
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
        "point_size",
        "digits",
        "stops_level",
    ),
    # 売買のトリガー: 足ごとの乖離の水準（その日の 8% 乖離の線）と、その元の EMA。
    plots=(PlotDecl("ema", "price"), PlotDecl(LEVEL_SERIES, "price")),
)
