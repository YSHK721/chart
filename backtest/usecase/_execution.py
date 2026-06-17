"""usecase 内ヘルパー: 約定計算（PROCESS §4・§5）。

UC-001 Interactor が呼ぶ純粋関数群。外部 I/O・pandas を持たず、domain の
Order/Position/Deal/TradeRecord のみを操作する（CLEAN_ARCH §9 注記が許容する
usecase 内配置。adapter 配置だと usecase→adapter の依存逆流になるため不可）。

usecase 層は domain のみ依存可。
"""
from __future__ import annotations

from backtest.domain.order import Order
from backtest.domain.position import Position


def fill_market_order(order: Order, *, bid: float, ask: float) -> Position:
    """成行注文を約定し Position を生成する（PROCESS §4.1）。

    買い=Ask・売り=Bid で約定する（スプレッドは entry_price に内包）。
    """
    entry_price = ask if order.side == "buy" else bid
    return Position(side=order.side, volume=order.volume, entry_price=entry_price)


def fill_buy_limit(
    order: Order, *, ask: float, tick_time, expire_time
) -> "Position | str | None":
    """BuyLimit 指値注文を 1 ティック分評価する（PROCESS §4.2）。

    返り値:
        Position … Ask <= limit に到達して約定した（約定価格＝指値・スリッページ0）。
        "expired" … tick_time >= expire_time で期限失効した（約定に優先）。
        None … 未到達かつ期限内（保留継続）。
    """
    if tick_time >= expire_time:
        return "expired"
    if ask <= order.price:
        return Position(side=order.side, volume=order.volume, entry_price=order.price)
    return None


def check_sltp_hit(
    position: Position,
    *,
    high: float,
    low: float,
    sl: float | None,
    tp: float | None,
    sltp_tie: str,
) -> "str | None":
    """保有ポジに対し当該足の SL/TP ヒットを判定する（PROCESS §5）。

    買い: low<=sl で SL ヒット・high>=tp で TP ヒット。
    売り: high>=sl で SL ヒット・low<=tp で TP ヒット。
    同足で両方に到達した場合は決定論 #3（sltp_tie="sl"）で SL を優先する。
    返り値: "sl" / "tp" / None（ヒットなし）。
    """
    if position.side == "buy":
        sl_hit = sl is not None and low <= sl
        tp_hit = tp is not None and high >= tp
    else:
        sl_hit = sl is not None and high >= sl
        tp_hit = tp is not None and low <= tp

    if sl_hit and tp_hit:
        return "sl" if sltp_tie == "sl" else "tp"
    if sl_hit:
        return "sl"
    if tp_hit:
        return "tp"
    return None
