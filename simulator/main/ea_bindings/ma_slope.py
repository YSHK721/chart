"""MA_Slope_EA の束縛。ISSUE-502 段階 4A。"""
from __future__ import annotations

from simulator.adapter.indicator import madiff as madiff_indicator
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.ma_slope import MaSlope
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import source_for


def build_registry(df, *, ma_period: int) -> PandasIndicatorRegistry:
    """EMA(ma_period, close) を "ema" として登録した IndicatorPort 実装を構築する。

    MaSlope は indicators.get("ema") を参照する（ma_slope.py を Read で実証）。
    """
    ema = madiff_indicator.ema_series(df["close"], ma_period)
    return PandasIndicatorRegistry({"ema": ema})


def _factory_ma_slope(ctx: EaBuildContext):
    # 読む形式はデータ実体が決める（ISSUE-511 段階 8-B）。本 EA が宣言するのは
    # 「close から EMA を作る」ことだけであり、TAB か comma かは `sources` の内側の話。
    # frame と読み手は**同じ 1 回の解決**から受け取る（形式判定を 2 回発行しない）。
    source = source_for(ctx.data_path)
    registry = build_registry(source.frame, ma_period=ctx.param("ma_period"))
    return MaSlope(), registry, source.repository


BINDING = EaBinding(
    name="MA_Slope_EA",
    strategy_type=MaSlope,
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
