"""MA_Slope_Pending_EA の束縛。ISSUE-502 段階 4A。"""
from __future__ import annotations

from simulator.adapter.indicator import madiff as madiff_indicator
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.adapter.strategy.ma_slope_pending import MaSlopePending
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import load_mt5_dataframe

#: 発注価格を当該バー始値クォートから作る EA が共有する戦略パラメータ宣言。
PENDING_STRATEGY_PARAMS = (
    # MaSlopePending が参照する追加パラメータ（MaSlope/TC は未参照のため無害）。
    "digits",
    "stops_level",
    "entry_offset_points",
    "entry_type",
)


def build_registry(df, *, ma_period: int) -> PandasIndicatorRegistry:
    """EMA に加え当該バー始値 "open" と "spread"（ポイント）を登録した IndicatorPort。

    MaSlopePending は確定足 EMA（"ema"）でシグナルを出しつつ、ペンディング価格を当該バー
    始値クォート（bid=open / ask=open+spread×point）から算出するため "open"/"spread" 系列を
    参照する（ma_slope_pending.py を Read で実証）。spread は MT5 CSV の <SPREAD>（ポイント）。
    """
    ema = madiff_indicator.ema_series(df["close"], ma_period)
    return PandasIndicatorRegistry(
        {
            "ema": ema,
            "open": df["open"].astype(float).reset_index(drop=True),
            "spread": df["<SPREAD>"].astype(float).reset_index(drop=True),
        }
    )


def _factory_ma_slope_pending(ctx: EaBuildContext):
    # 指値/逆指値版。MA_Slope_EA と同じ MT5 CSV を読み、open/spread も registry に載せる。
    df = load_mt5_dataframe(ctx.data_path)
    registry = build_registry(df, ma_period=ctx.param("ma_period"))
    return MaSlopePending(), registry, Mt5CsvOHLCRepository()


BINDING = EaBinding(
    name="MA_Slope_Pending_EA",
    build=_factory_ma_slope_pending,
    strategy_params=PENDING_STRATEGY_PARAMS,
)
