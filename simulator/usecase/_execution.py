"""usecase 内ヘルパー: 約定計算（PROCESS §4・§5）。

UC-001 Interactor が呼ぶ純粋関数群。外部 I/O・pandas を持たず、domain の
Order/Position/Deal/TradeRecord のみを操作する（CLEAN_ARCH §9 注記が許容する
usecase 内配置。adapter 配置だと usecase→adapter の依存逆流になるため不可）。

usecase 層は domain のみ依存可。
"""
from __future__ import annotations

from simulator.domain.order import Order
from simulator.domain.position import Position


def fill_market_order(
    order: Order,
    *,
    bid: float,
    ask: float,
    spread: int = 0,
    point_size: float = 0.0,
) -> Position:
    """成行注文を約定し Position を生成する（PROCESS §4.1）。

    買い=Ask・売り=Bid で約定する（スプレッドは entry_price に内包）。

    スプレッド対応（後方互換・実 MT5 突合）:
        spread > 0 のとき買いは ``bid + spread * point_size`` で約定する
        （実 MT5 の Ask = Bid + spread×point に整合。fixture report_900005560 の
        初回 buy=39412.0 ＝ open(bid)39402.0 + 100×0.1 を再現する）。売りは bid で約定。
        spread=0（既定・未指定）のときは従来どおり買い=ask・売り=bid で、
        既存の呼び出し・結果と完全に同一である。
    """
    if order.side == "buy":
        entry_price = bid + spread * point_size if spread > 0 else ask
    else:
        entry_price = bid
    return Position(side=order.side, volume=order.volume, entry_price=entry_price)


def close_price_for(side: str, *, bid: float, ask: float) -> float:
    """保有玉 1 件の決済価格を約定価格ルールで一意に決める（PROCESS §4・§6）。

    約定価格ルール（一元化）:
        新規 buy=ask / 新規 sell=bid（成行は fill_market_order が担う）
        long（buy 玉）決済=bid / short（sell 玉）決済=ask
    long を閉じるのは売り（=bid で約定）、short を閉じるのは買い（=ask で約定）に
    対応する。reverse 決済・SL/TP 後の強制決済（stop_out）で共通して用いる。
    spread=0（bid==ask）のときは従来の close 約定と完全一致する（後方互換）。
    """
    return bid if side == "buy" else ask


def derive_quotes(
    bar: object, *, entry_price_basis: str, point_size: float
) -> "tuple[float, float, int, float]":
    """約定価格基準（config）から当該足の (bid, ask, fill_spread, fill_point) を導く。

    config ゲートを 1 箇所へ集約する（PROCESS §4・実 MT5 突合）:
        "close"（既定・後方互換）:
            bid=ask=close・spread 無視（fill_spread=0）で従来挙動と完全一致。
        "current_open"（原典 .mq5・新規バー現値約定）:
            bid=open / ask=open + spread×point（実 MT5 Ask=Bid+spread×point）。
            fill_market_order の spread 引数に bar.spread・point_size を渡し、
            買い建ては open+spread×point、short の reverse 決済（=ask）も対称に
            spread を内包する（cycle4 バグ①）。
    """
    if entry_price_basis == "current_open":
        bid = bar.open
        ask = bar.open + bar.spread * point_size
        return bid, ask, bar.spread, point_size
    return bar.close, bar.close, 0, 0.0


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


def fill_pending_order(
    order: Order, *, bid: float, ask: float
) -> "Position | None":
    """ペンディング注文（指値/逆指値）を 1 ティック分評価する（PROCESS §4.2 拡張）。

    実 MT5 のトリガ条件（約定価格＝注文価格・スリッページ 0）:
        buy_limit  : Ask <= price（現値が指値まで下落して買い）
        sell_limit : Bid >= price（現値が指値まで上昇して売り）
        buy_stop   : Ask >= price（現値が逆指値まで上昇して買い）
        sell_stop  : Bid <= price（現値が逆指値まで下落して売り）

    返り値:
        Position … トリガ条件成立で約定した（entry_price=order.price）。
        None      … 未到達（保留継続）。

    時間失効は扱わない（本 EA は毎バー未約定分を取消・再設置するため 1 バー寿命）。
    fill_buy_limit（既存・expire 付き）とは別関数として並存させる（既存経路は不変）。
    """
    price = order.price
    kind = order.kind
    if kind == "buy_limit":
        triggered = ask <= price
    elif kind == "sell_limit":
        triggered = bid >= price
    elif kind == "buy_stop":
        triggered = ask >= price
    elif kind == "sell_stop":
        triggered = bid <= price
    else:
        return None
    if not triggered:
        return None
    return Position(side=order.side, volume=order.volume, entry_price=price)


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


def check_sltp_hit_at_tick(
    position: Position,
    *,
    price: float,
    sl: float | None,
    tp: float | None,
    sltp_tie: str,
) -> "str | None":
    """単一ティック価格 1 点で保有ポジの SL/TP ヒットを判定する（every-tick #2）。

    every-tick モードでは 1 ティック＝1 価格のため、bar の high/low の代わりに
    到達ティック価格 ``price`` 1 点で判定する。単一価格 p は bar 版へ high=low=p を
    渡すことに等しく、決定論 #3（sltp_tie）の同点解消ロジックを継承する。
    既存の bar 経路（check_sltp_hit）は不変。返り値: "sl" / "tp" / None。
    """
    return check_sltp_hit(
        position, high=price, low=price, sl=sl, tp=tp, sltp_tie=sltp_tie
    )
