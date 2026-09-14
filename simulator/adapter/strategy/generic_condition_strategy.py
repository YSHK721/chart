"""A-GenericConditionStrategy: spec 駆動の汎用条件戦略（StrategyPort 実装・Phase 6 F-8）。

TBD-11 の「entry = レベル2（比較演算＋AND 連鎖＋履歴参照）」「SL/TP = 点数固定」を、
戦略ごとの手書きコードではなく **spec（:class:`EntryConditions`）＋点数換算単一ソース**
（:func:`sltp_from_points`）で表現する汎用戦略。既存 6 戦略・run_backtest・エンジンは無改変。

ポート契約（tc24051901 / pro_fit_band と同形）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
      1. warmup ガード: bar_index < max_shift（両側条件の最大 shift）→ []
      2. held_sides で同方向重複を抑止（tc/pro_fit と同一）
      3. entry_long → 成行買い / entry_short → 成行売り（long を先に評価）
      4. Order は kind="market"・price=None（約定価格は execution が解決）・
         SL/TP は :func:`sltp_from_points`（点数固定・単一ソース）
    on_position_check → "hold"（反転決済なし・固定 SL/TP のみ）

基準価格系列（§3.5.5 実証・sizing_ports の単一ソース再利用）:
    ``required_price_series(entry_price_basis)`` が "close"/"open" を決める。
    系列が registry に無い場合は例外を伝播させる（fail-stop・無音の誤建値を作らない）。

DIP: domain の :class:`EntryConditions` は pandas/registry を知らない。本 adapter が
    ``sample(name, shift) = indicators.get(name).iloc[bar_index-shift]`` で橋渡しする。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.entry_conditions import EntryConditions
from simulator.domain.order import Order
from simulator.domain.sltp import sltp_from_points
from simulator.usecase.ports import StrategyPort
from simulator.usecase.sizing_ports import required_price_series


class GenericConditionStrategy(StrategyPort):
    """spec（EntryConditions）で駆動する汎用条件戦略（固定 SL/TP）。"""

    def __init__(
        self,
        *,
        entry_long: EntryConditions,
        entry_short: EntryConditions,
        entry_price_basis: str = "close",
    ) -> None:
        self._entry_long = entry_long
        self._entry_short = entry_short
        # 未知の基準は required_price_series が例外（無音で "close" へ倒さない）。
        self._price_series = required_price_series(entry_price_basis)
        self._max_shift = max(entry_long.max_shift, entry_short.max_shift)
        self._config: Any = None
        self._indicators: Any = None

    def on_init(self, config: Any, indicators: Any) -> None:
        # SL/TP>0 を拒否しない（点数 0 は sltp_from_points が None にする）。
        self._config = config
        self._indicators = indicators

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        if bar_index < self._max_shift:  # warmup: 過去参照が範囲外
            return []

        def sample(name: str, shift: int) -> float:
            # 系列未登録は IndicatorPort.get が例外（fail-stop・伝播させる）。
            return indicators.get(name).iloc[bar_index - shift]

        held_sides = self._held_sides(account)

        # long を先に評価（両側同時成立時の決定性）。空側は __bool__=False で無効。
        if self._entry_long and "buy" not in held_sides and self._entry_long.matches(sample):
            return [self._build_order("buy", indicators, bar_index)]
        if self._entry_short and "sell" not in held_sides and self._entry_short.matches(sample):
            return [self._build_order("sell", indicators, bar_index)]
        return []

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"  # 反転決済なし（固定 SL/TP のみ）

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _held_sides(account: Any) -> "set[str]":
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}

    def _build_order(self, side: str, indicators: Any, bar_index: int) -> Order:
        cfg = self._config
        base_price = float(indicators.get(self._price_series).iloc[bar_index])
        sl, tp = sltp_from_points(
            side,
            base_price,
            cfg["stop_loss_points"],
            cfg["take_profit_points"],
            cfg["point_size"],
        )
        return Order(
            side=side,
            kind="market",
            volume=cfg["lot_size"],
            price=None,
            sl=sl,
            tp=tp,
        )
