"""MA_Slope_EA の束縛。ISSUE-502 段階 4A。"""
from __future__ import annotations

from simulator.adapter.indicator import madiff as madiff_indicator
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.adapter.strategy.ma_slope import MaSlope
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import load_mt5_dataframe


def build_registry(df, *, ma_period: int) -> PandasIndicatorRegistry:
    """EMA(ma_period, close) を "ema" として登録した IndicatorPort 実装を構築する。

    MaSlope は indicators.get("ema") を参照する（ma_slope.py を Read で実証）。
    """
    ema = madiff_indicator.ema_series(df["close"], ma_period)
    return PandasIndicatorRegistry({"ema": ema})


def _factory_ma_slope(ctx: EaBuildContext):
    # MA_Slope_EA は MT5 エクスポート形式（タブ区切り・<DATE>/<TIME>/<SPREAD>）を読む。
    df = load_mt5_dataframe(ctx.data_path)
    registry = build_registry(df, ma_period=ctx.param("ma_period"))
    return MaSlope(), registry, Mt5CsvOHLCRepository()


BINDING = EaBinding(
    name="MA_Slope_EA",
    build=_factory_ma_slope,
    strategy_params=(
        # MaSlope が参照する追加パラメータ（他戦略は未参照のため無害）。
        "slope_shift",
        "slope_min_points",
        # MaSlope の NormalizeLot（原典 MA_Slope_EA.mq5:157）が参照する銘柄仕様。
        # SymbolInfoDouble(SYMBOL_VOLUME_MIN/MAX/STEP) 相当（ISSUE-445 段階 1）。
        "volume_min",
        "volume_max",
        "volume_step",
    ),
)
