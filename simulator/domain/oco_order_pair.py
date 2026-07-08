"""E-OcoOrderPair: OCO 発注対（Value Object・詳細設計 §3.4・D1）。

D1: 戦略はセグメント先頭で market ロング 1 件を sl=S/tp=T 付きで発注。OCO は engine の
SL/TP 監視（同一バー両到達=SL 優先）と end_of_test 清算（金曜引け）で実現する。よって
本 VO は market エントリ Order を内包し、別個の stop/limit 子注文は持たない（単玉）。
as_orders() は [entry] を返す。

domain 層は外部依存ゼロ（標準ライブラリのみ）。
"""
from __future__ import annotations

from dataclasses import dataclass

from simulator.domain.order import Order
from simulator.domain.volatility_band import VolatilityBand


@dataclass(frozen=True)
class OcoOrderPair:
    entry: Order
    band: VolatilityBand

    def __post_init__(self) -> None:
        if self.entry.kind != "market" or self.entry.side != "buy":
            raise ValueError("OcoOrderPair.entry は market buy のみ（ロング専用・D1）")
        if self.entry.sl is None or self.entry.tp is None:
            raise ValueError("OcoOrderPair.entry は sl/tp 必須（S/T 監視）")

    def as_orders(self) -> list[Order]:
        return [self.entry]
