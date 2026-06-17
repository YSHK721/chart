"""TC24051901 戦略（StrategyPort 実装・SPEC §3.1）。

MADiff ゼロクロス両建て。固定 SL/TP 付き成行。毎ティック評価（新規バー判定なし）。

エントリ:
    買い: prev < 0 && curr > 0 かつ 同方向ポジ無し → 成行買い
    売り: prev > 0 && curr < 0 かつ 同方向ポジ無し → 成行売り
決済（発注時固定 SL/TP のみ・反転決済なし）:
    買い: price=Ask, sl=price−StopLoss×point, tp=price+TakeProfit×point
    売り: price=Bid, sl=price+StopLoss×point, tp=price−TakeProfit×point

ポート契約（usecase Interactor に整合）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
    curr/prev は indicators.get("madiff") の bar_index / bar_index-1。
    エントリ基準価格は indicators.get("close")[bar_index]（最小骨格 spread=0 で
    Ask=Bid=close。実 spread 反映は spread_model 接続時＝範囲外）。Order は
    kind="market"・price=None（約定価格は execution で解決）、sl/tp は絶対価格。
"""
from __future__ import annotations

from typing import Any

from backtest.domain.order import Order
from backtest.usecase.ports import StrategyPort


class TC24051901(StrategyPort):
    """MADiff ゼロクロス両建て戦略（固定 SL/TP）。"""

    def __init__(self) -> None:
        self._config: dict | None = None
        self._indicators: Any = None

    def on_init(self, config: Any, indicators: Any) -> None:
        self._config = config
        self._indicators = indicators

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        if bar_index < 1:  # 境界: prev 不在ならクロス判定不可
            return []
        madiff = indicators.get("madiff")
        curr = madiff.iloc[bar_index]
        prev = madiff.iloc[bar_index - 1]
        held_sides = self._held_sides(account)

        if prev < 0 and curr > 0 and "buy" not in held_sides:
            return [self._build_order("buy", indicators, bar_index)]
        if prev > 0 and curr < 0 and "sell" not in held_sides:
            return [self._build_order("sell", indicators, bar_index)]
        return []

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        # 反転決済なし（固定 SL/TP のみ）。
        return "hold"

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _held_sides(account: Any) -> set[str]:
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}

    def _build_order(self, side: str, indicators: Any, bar_index: int) -> Order:
        cfg = self._config
        price = float(indicators.get("close").iloc[bar_index])
        sl_pts = cfg["stop_loss_points"] * cfg["point_size"]
        tp_pts = cfg["take_profit_points"] * cfg["point_size"]
        if side == "buy":
            sl, tp = price - sl_pts, price + tp_pts
        else:
            sl, tp = price + sl_pts, price - tp_pts
        return Order(
            side=side,
            kind="market",
            volume=cfg["lot_size"],
            price=None,
            sl=sl,
            tp=tp,
        )
