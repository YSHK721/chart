"""StopEntryProbe_EA の束縛。ISSUE-502 段階 4A。"""
from __future__ import annotations

from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.ma_slope_pending import (
    PENDING_STRATEGY_PARAMS,
    build_registry,
)
from simulator.main.ea_bindings.sources import load_mt5_dataframe


def _factory_stop_entry_probe(ctx: EaBuildContext):
    # 逆指値プローブ（両建て BuyStop+SellStop・OCO・足途中ティック再アーム）。MT5 CSV を読む。
    #   発注クォートは engine が on_tick へ渡すティック bid/ask を使うため指標非依存だが、
    #   registry IF を満たすため pending 用 registry（ema/open/spread）を共用する（戦略は未参照）。
    df = load_mt5_dataframe(ctx.data_path)
    registry = build_registry(df, ma_period=ctx.param("ma_period"))
    return StopEntryProbe(), registry, Mt5CsvOHLCRepository()


BINDING = EaBinding(
    name="StopEntryProbe_EA",
    build=_factory_stop_entry_probe,
    # 発注価格の作り方は MA_Slope_Pending と同型（宣言も同じものを参照し、写さない）。
    strategy_params=PENDING_STRATEGY_PARAMS,
)
