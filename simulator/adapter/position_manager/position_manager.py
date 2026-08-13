"""A-PositionManager: 建玉変更の適用器（PositionManagerPort 実装・Phase 7 FR-07/08）.

TrailingRule / PartialCloseRule（domain 値オブジェクト）を DI し、1 評価点で保有玉 1 件を
:class:`PositionDirective` へ写す。規則（domain）と適用（本 adapter）と口座反映（Interactor）
を分離する（SRP）。既定 :class:`NullPositionManager` は常に ``None`` を返し既定経路を
byte 不変に保つ（LSP）。

トレーリング粒度: ``trailing_granularity`` と ``evaluate`` の ``granularity`` が一致する
ときのみトレーリング規則を作動させる（bar/tick を spec で選択・両粒度対応）。

部分決済「1 回のみ」（裁定 2026-08-13）: 一度部分決済した玉は再発火させない。玉の同定は
(side, entry_time, entry_price) の安定キーで行う（部分決済後の残玉は同 entry を維持するため
同一キー＝再発火しない・単一玉前提 YAGNI）。純粋 domain 規則に状態を持たせず、適用済み
集合を本適用器が所有する。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.position_directive import PositionDirective
from simulator.usecase.ports import PositionManagerPort


class NullPositionManager(PositionManagerPort):
    """常に None（無変更）を返す既定実装（LSP・既定経路 byte 不変）。"""

    def evaluate(
        self, *, ot: Any, ref_price: float, granularity: str, account: Any = None
    ) -> "PositionDirective | None":
        return None


class PositionManager(PositionManagerPort):
    """トレーリング/部分決済規則を適用して PositionDirective を合成する。"""

    def __init__(
        self,
        *,
        trailing_rule: Any = None,
        partial_close_rule: Any = None,
        trailing_granularity: str = "bar",
        volume_step: float = 0.0,
    ) -> None:
        self._trailing = trailing_rule
        self._partial = partial_close_rule
        self._trailing_granularity = trailing_granularity
        self._volume_step = volume_step
        # 部分決済「1 回のみ」の適用済みキー集合（run 内でのみ保持）。
        self._partial_done: "set[tuple]" = set()

    def evaluate(
        self, *, ot: Any, ref_price: float, granularity: str, account: Any = None
    ) -> "PositionDirective | None":
        close_volume: "float | None" = None
        close_price: "float | None" = None
        new_sl: "float | None" = None

        # 部分決済（1 回のみ・粒度に依らず run のネイティブ評価点で発火）。
        if self._partial is not None:
            key = self._partial_key(ot)
            if key not in self._partial_done:
                cv = self._partial.close_volume(
                    ot.position.side,
                    ot.entry_price,
                    ref_price,
                    ot.position.volume,
                    self._volume_step,
                )
                if cv is not None:
                    close_volume = cv
                    self._partial_done.add(key)
                    # フィル価格: bar 粒度＝トリガー水準（部分 TP のレベルフィル・極値でない）／
                    #   tick 粒度＝現在価格（ref_price＝close_price_for=bid/ask・忠実）。
                    #   到達検出（極値 touch）とフィル価格を分離する（依頼者裁定 2026-08-13）。
                    if granularity == "bar":
                        close_price = self._partial.fill_price(ot.position.side, ot.entry_price)
                    else:
                        close_price = ref_price

        # トレーリング（設定粒度と一致する評価点でのみ作動）。
        if self._trailing is not None and granularity == self._trailing_granularity:
            ns = self._trailing.new_stop(
                ot.position.side, ot.entry_price, ref_price, ot.sl
            )
            if ns is not None:
                new_sl = ns

        if new_sl is None and close_volume is None:
            return None
        return PositionDirective(
            new_sl=new_sl, new_tp=None, close_volume=close_volume, close_price=close_price
        )

    @staticmethod
    def _partial_key(ot: Any) -> tuple:
        """部分決済適用済みの安定キー（残玉縮小後も不変・単一玉前提）。"""
        return (ot.position.side, ot.entry_time, ot.entry_price)
