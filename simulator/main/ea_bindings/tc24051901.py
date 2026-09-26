"""TC24051901（既定 EA・comma 形式・MADiff 指標）の束縛。ISSUE-502 段階 4A。

本 EA は登録表の外側にある**既定フォールバック先**である（未登録 ea_name はここへ落ちる）。
その関係は `ea_bindings/__init__.py` の選択規則が 1 箇所で持つ。
"""
from __future__ import annotations

from simulator.adapter.indicator import madiff as madiff_indicator
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.tc24051901 import TC24051901
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import load_dataframe, ohlc_repository_for


def build_registry(df, *, ma_period: int, ma_method: str) -> PandasIndicatorRegistry:
    """MADiff 系列と close 系列を登録した IndicatorPort 実装を構築する。

    TC24051901 は indicators.get("madiff") と indicators.get("close") を参照する
    （tc24051901.py を Read で実証）。両系列を事前計算して登録する。
    """
    madiff_series = madiff_indicator.madiff(df, period=ma_period, method=ma_method)
    return PandasIndicatorRegistry({"madiff": madiff_series, "close": df["close"]})


def _factory_tc24051901(ctx: EaBuildContext):
    # 既定経路（TC24051901・comma 形式・MADiff 指標）= 従来挙動を不変に保つ。
    df = load_dataframe(ctx.data_path)
    registry = build_registry(
        df, ma_period=ctx.param("ma_period"), ma_method=ctx.param("ma_method")
    )
    return TC24051901(), registry, ohlc_repository_for(ctx.data_path)


BINDING = EaBinding(
    name="TC24051901", build=_factory_tc24051901, strategy_type=TC24051901
)
