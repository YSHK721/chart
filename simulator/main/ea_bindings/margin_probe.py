"""MarginProbe_EA の束縛（証拠金維持率の動作確認専用 EA）。"""
from __future__ import annotations

from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.strategy.margin_probe import MarginProbe
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext
from simulator.main.ea_bindings.sources import series_or_data_error, source_for


def build_registry(df) -> PandasIndicatorRegistry:
    """当該バー始値 "open" と "spread"（ポイント）だけを登録した IndicatorPort 実装。

    MarginProbe は指標でシグナルを出さない。建値クォート（ask = open + spread × point）
    から発注量を導くための 2 系列だけを読む。気配幅の列が無いデータは既定 0 で補わず
    Fail-Stop する（`series_or_data_error`・ma_slope_pending と同じ規則）。
    """
    return PandasIndicatorRegistry(
        {
            "open": series_or_data_error(df, "open"),
            "spread": series_or_data_error(df, "spread"),
        }
    )


def _factory_margin_probe(ctx: EaBuildContext):
    # 読む形式はデータ実体が決める（ISSUE-511 段階 8-B）。frame と読み手は**同じ 1 回の
    #   解決**から受け取る（形式判定を 2 回発行しない）。registry は構築パラメータを要らない。
    source = source_for(ctx.data_path)
    registry = build_registry(source.frame)
    return MarginProbe(), registry, source.repository


BINDING = EaBinding(
    name="MarginProbe_EA",
    strategy_type=MarginProbe,
    build=_factory_margin_probe,
    # MarginProbe.on_init が発注量の導出に読むもの: 狙いの維持率＋口座・銘柄仕様。
    strategy_params=(
        "margin_level_target",
        "leverage",
        "contract_size",
        "volume_min",
        "volume_max",
        "volume_step",
    ),
)
