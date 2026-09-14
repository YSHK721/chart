"""NullMarketDataRepository: バー系列を 1 本も返さない `MarketDataPort` 実装。

1. 層名/責務:
    adapter 層（Repository）。データ所在（``source_ref``）を読まず、常に空の
    ``list[Bar]`` を返す。価格系列を供給しない実行経路
    （`Math calculations`＝`TickModelSpec.requires_market_data is False`）で
    Composition Root が注入する。バーの発明は行わない。

2. 含む構造:
    NullMarketDataRepository（`MarketDataPort.load` を空列で実装）。

3. 元 MQL 対応:
    MT5 の `Math calculations`（`Model=3`）は価格系列を供給しない（基本設計 §4.5.2）。

4. 依存:
    標準: typing
    外部: なし
    プロジェクト内: simulator.domain.bar（Bar）/ simulator.usecase.ports（MarketDataPort）

LSP（`MarketDataPort.load` の契約・ports.py 実読）:
    事後条件は「``list[domain.Bar]``（時刻昇順）を返す」。空列は時刻昇順を自明に満たす。
    ``source_ref`` の事前条件（パス様の参照）は**強化しない**——``None`` を含むどの値でも
    受け、参照しない。例外契約も強化しない（I/O を行わないため送出し得ない）。
    したがって他の `MarketDataPort` 実装と相互置換できる。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.bar import Bar
from simulator.usecase.ports import MarketDataPort


class NullMarketDataRepository(MarketDataPort):
    """常に空のバー列を返す（データ所在を参照しない）。"""

    def load(self, source_ref: Any = None, timeframe: Any = None, period: Any = None) -> "list[Bar]":
        return []
