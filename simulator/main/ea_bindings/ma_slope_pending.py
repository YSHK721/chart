"""MA_Slope_Pending_EA の束縛。ISSUE-502 段階 4A。"""
from __future__ import annotations

from simulator.adapter.indicator import madiff as madiff_indicator
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.ma_slope_pending import MaSlopePending
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import series_or_data_error, source_for

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
    参照する（ma_slope_pending.py を Read で実証）。気配幅は整数ポイントであり、その意味は
    形式に依らない（MT5 表記と marketdata 表記は同じ量・ISSUE-511）。
    列名の差は `sources` が正規化済みであり、本モジュールは正規化後の名前だけを知る。
    列が無いデータを渡された場合は既定 0 で補わず Fail-Stop する（`series_or_data_error`）。
    """
    ema = madiff_indicator.ema_series(df["close"], ma_period)
    return PandasIndicatorRegistry(
        {
            "ema": ema,
            "open": series_or_data_error(df, "open"),
            "spread": series_or_data_error(df, "spread"),
        }
    )


def _factory_ma_slope_pending(ctx: EaBuildContext):
    # 指値/逆指値版。読む形式はデータ実体が決める（ISSUE-511 段階 8-B）。
    # frame と読み手は**同じ 1 回の解決**から受け取る（形式判定を 2 回発行しない）。
    source = source_for(ctx.data_path)
    registry = build_registry(source.frame, ma_period=ctx.param("ma_period"))
    return MaSlopePending(), registry, source.repository


BINDING = EaBinding(
    name="MA_Slope_Pending_EA",
    build=_factory_ma_slope_pending,
    strategy_params=PENDING_STRATEGY_PARAMS,
)
