"""WeeklyVolBand_EA の束縛。ISSUE-502 段階 4A。"""
from __future__ import annotations

from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.weekly_vol_band import WeeklyVolBand, make_weekly_vol_band
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import load_dataframe, ohlc_repository_for


def build_registry(df) -> PandasIndicatorRegistry:
    """セグメント先頭 open を "open" として登録した IndicatorPort 実装を構築する。

    WeeklyVolBand は indicators.get("open").iloc[0] でセグメント先頭バー open（=O）を
    参照する（weekly_vol_band.py を Read で実証）。registry IF を満たすため open 系列の
    みを登録する（他指標は未参照）。pandas は Composition Root 側に閉じる。
    """
    return PandasIndicatorRegistry(
        {"open": df["open"].astype(float).reset_index(drop=True)}
    )


def _factory_weekly_vol_band(ctx: EaBuildContext):
    # 週次ボラ・バンド戦略（詳細設計 §5.1・§11 D1）。comma 形式 CSV を読み、セグメント
    # 先頭 open のみを "open" registry に載せる。構築は共有ファクトリへ一元化（🟡-3）。
    df = load_dataframe(ctx.data_path)
    registry = build_registry(df)
    strategy = make_weekly_vol_band(
        forecast=ctx.param("weekly_forecast"),
        p_tp=ctx.param("weekly_p_tp"),
        capital=ctx.param("weekly_capital"),
        f_risk=ctx.param("weekly_f_risk"),
    )
    return strategy, registry, ohlc_repository_for(ctx.data_path)


BINDING = EaBinding(
    name="WeeklyVolBand_EA",
    build=_factory_weekly_vol_band,
    strategy_type=WeeklyVolBand,
)
