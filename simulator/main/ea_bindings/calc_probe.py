"""CalcProbe_EA の束縛（計算処理の動作確認専用 EA）。"""
from __future__ import annotations

from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.calc_probe import CalcProbe
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import load_dataframe, ohlc_repository_for


def build_registry(df, ma_period: int) -> PandasIndicatorRegistry:
    """始値と終値の SMA（期間 ``ma_period``）を登録した IndicatorPort 実装を構築する。"""
    close = df["close"].astype(float)
    sma = close.rolling(window=int(ma_period), min_periods=int(ma_period)).mean()
    return PandasIndicatorRegistry(
        {
            "open": df["open"].astype(float).reset_index(drop=True),
            "sma": sma.reset_index(drop=True),
        }
    )


def _factory_calc_probe(ctx: EaBuildContext):
    df = load_dataframe(ctx.data_path)
    registry = build_registry(df, ctx.param("ma_period"))
    return CalcProbe(), registry, ohlc_repository_for(ctx.data_path)


BINDING = EaBinding(
    name="CalcProbe_EA",
    strategy_type=CalcProbe,
    build=_factory_calc_probe,
    # CalcProbe.on_init がロットを刻みへ丸めるのに読む銘柄仕様を含む。
    strategy_params=("lot_size", "volume_min", "volume_max", "volume_step"),
)
