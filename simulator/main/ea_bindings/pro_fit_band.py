"""PRO_fit_Band_EA（#5・my_first_ea）の束縛。ISSUE-502 段階 4A。"""
from __future__ import annotations

from simulator.adapter.indicator import madiff as madiff_indicator
from simulator.adapter.indicator.ema_adx_di import compute_adx_with_di
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.pro_fit_band import ProFitBand
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import load_dataframe, ohlc_repository_for


def build_registry(df, *, ma_period: int, adx_period: int) -> PandasIndicatorRegistry:
    """EMA(ma_period, close)・ADX(adx_period)/+DI/−DI・close を登録した IndicatorPort。

    ProFitBand は indicators.get("ema"/"adx"/"plus_di"/"minus_di"/"close") を参照する
    （pro_fit_band.py を Read で実証）。EMA は adapter の ``ema_series``、ADX/±DI は
    ``compute_adx_with_di``（原典 iADX 再現・SPEC §3.5）で事前計算して登録する。
    close は df["close"] をそのまま登録する（TC 既定 registry と同形）。
    """
    ema = madiff_indicator.ema_series(df["close"], ma_period)
    adx, plus_di, minus_di = compute_adx_with_di(
        df["high"], df["low"], df["close"], period=adx_period
    )
    return PandasIndicatorRegistry(
        {
            "ema": ema,
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "close": df["close"],
        }
    )


def _factory_pro_fit_band(ctx: EaBuildContext):
    # comma 形式 CSV を読み、EMA/ADX/±DI/close registry を供給する。従来 build_interactor に
    # 分岐が無く生成不能だった件を 1 エントリで解消（🟡-3）。
    df = load_dataframe(ctx.data_path)
    registry = build_registry(
        df, ma_period=ctx.param("ma_period"), adx_period=ctx.param("adx_period")
    )
    return ProFitBand(), registry, ohlc_repository_for(ctx.data_path)


BINDING = EaBinding(
    name="PRO_fit_Band_EA",
    build=_factory_pro_fit_band,
    # ProFitBand が参照する追加パラメータ（他戦略は未参照のため無害）。既定は
    # 原典 .mq5 の Adx_Min=22.0（🟡-3）。既定値の所在は build_interactor の宣言 1 箇所。
    strategy_params=("adx_min",),
)
