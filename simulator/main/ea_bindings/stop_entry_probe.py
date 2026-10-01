"""StopEntryProbe_EA の束縛。ISSUE-502 段階 4A。"""
from __future__ import annotations

from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.ma_slope_pending import (
    PENDING_STRATEGY_PARAMS,
    build_registry,
)
from simulator.main.ea_bindings.sources import source_for


def _factory_stop_entry_probe(ctx: EaBuildContext):
    # 逆指値プローブ（両建て BuyStop+SellStop・OCO・足途中ティック再アーム）。
    #   発注クォートは engine が on_tick へ渡すティック bid/ask を使うため指標非依存だが、
    #   registry IF を満たすため pending 用 registry（ema/open/spread）を共用する（戦略は未参照）。
    #   読む形式はデータ実体が決める（ISSUE-511 段階 8-B）。frame と読み手は
    #   **同じ 1 回の解決**から受け取る（形式判定を 2 回発行しない）。
    source = source_for(ctx.data_path)
    registry = build_registry(source.frame, ma_period=ctx.param("ma_period"))
    return StopEntryProbe(), registry, source.repository


BINDING = EaBinding(
    name="StopEntryProbe_EA",
    strategy_type=StopEntryProbe,
    build=_factory_stop_entry_probe,
    # 発注価格の作り方は MA_Slope_Pending と同型（宣言も同じものを参照し、写さない）。
    strategy_params=PENDING_STRATEGY_PARAMS,
)
