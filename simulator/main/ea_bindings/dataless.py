"""バー系列を消費しない modelling の構成（A-1・ISSUE-397）。ISSUE-502 段階 4A。

登録表の外側にある**規則側の構成**である（ea_name では選ばれない。tick_model の宣言
TickModelSpec.requires_market_data が偽のときに選択規則が採る）。
"""
from __future__ import annotations

from simulator.adapter.indicator.null_registry import NullIndicatorRegistry
from simulator.adapter.repository.null_market_data import NullMarketDataRepository
from simulator.adapter.strategy.null_strategy import NullStrategy
from simulator.main.ea_bindings.binding import EaBinding, EaBuildContext


def _factory_dataless(_ctx: EaBuildContext):
    """`ctx.data_path` を**参照しない**（読むものが無いのが本経路の実体である）。

    既存の EA ファクトリは全て sources の CSV 読みで `data_path` を読むため（実測:
    ``data_path=None`` は市場データの load より前に factory の CSV 読みで DataError に
    なる）、データ供給の有無は **market_data 実体だけでなく本 3 点組の選択**で表す
    必要がある。返す 3 点は既存の Null 実装（Port ABC の実装＝LSP 維持）。
    """
    return NullStrategy(), NullIndicatorRegistry(), NullMarketDataRepository()


BINDING = EaBinding(name="__dataless__", build=_factory_dataless)
