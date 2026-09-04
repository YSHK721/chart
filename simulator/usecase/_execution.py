"""usecase 内ヘルパー: 約定計算（PROCESS §4・§5）。

UC-001 Interactor が呼ぶ純粋関数群。外部 I/O・pandas を持たず、domain の
Order/Position/Deal/TradeRecord のみを操作する（CLEAN_ARCH §9 注記が許容する
usecase 内配置。adapter 配置だと usecase→adapter の依存逆流になるため不可）。

usecase 層は domain のみ依存可。
"""
from __future__ import annotations

from typing import Callable

from simulator.domain.order import Order
from simulator.domain.position import Position


def mt5_bid_ask(base: float, *, spread: float, point: float) -> "tuple[float, float]":
    """MT5 執行クォート規約 ``Ask = Bid + spread×point`` の単一プリミティブ（ISSUE-100 🟡-1）。

    ``base`` を Bid とし ``ask = base + spread * point`` を返す。約定（open 基準）・含み損益評価
    （close 基準）・ペンディング/SL-TP 評価（tick 価格基準）で同一規約が三重にインライン化されて
    いたのを本関数へ一元化する（MT5 スプレッド規約の再校正時に 1 箇所修正で全経路へ反映）。
    演算順は ``spread * point`` を先に評価し、各インライン版（`base + spread * point`）と byte 一致。
    """
    return base, base + spread * point


def admit_orders(orders, spec) -> "list[Order]":
    """戦略が返した発注を実行経路へ受理する**唯一の門**（ISSUE-445 段階 3-C）。

    受理前に :meth:`Order.validate` で銘柄仕様の不変条件（side/kind の整合・volume の
    範囲と刻み・SL/TP の stops_level 距離）を検査し、違反時は ``InvalidPriceError`` を
    送出する。適合時は受け取った発注をそのまま（順序を保って）返す。

    **なぜ検査が要るか（欠落の実測）**: `Order.validate` は 2026-06 の domain 実装当初から
    存在したが、実行経路のどこからも呼ばれていなかった（実測 2026-08-26: 本番コードでの
    `.validate(` 呼出は `account_engine` の 2 件のみ）。そのため銘柄仕様に反する発注が
    そのまま約定し、MT5 では成立しない結果を無言で生成し得た。ISSUE-445 の RC-2
    （`MaSlope` が原典の `NormalizeLot` を欠き `volume=0.1 < volume_min=1.0` を発注していた）は
    まさにこの型であり、**本関数が結線されていれば発注の時点で赤になっていた**
    （`test_admission_would_have_caught_rc2` が実測で固定する）。

    **なぜ拒否＋続行ではなく送出か**: 原典 EA（`MA_Slope_EA.mq5` ほか）はいずれも
    `OrderSend` の前に自前で `NormalizeLot` を掛けており、**不正な発注はサーバへ到達しない**。
    つまり「MT5 サーバが不正発注をどう扱うか」は参照実装の定義域の外にある。ここで
    「拒否して続行」を選ぶと、参照実装が定義していない挙動を推測で作り込むことになり、
    かつ壊れたアダプタが「トレード 0 件」という一見正当な結果を無言で返す。よって
    受理側は判断せず送出し、人が裁定する（不正発注＝アダプタが原典から乖離した証拠）。
    """
    admitted = list(orders)
    for order in admitted:
        order.validate(spec)
    return admitted


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
        # spread>0 は MT5 規約 Ask=Bid+spread×point（mt5_bid_ask）で約定。spread=0 は従来 ask。
        entry_price = mt5_bid_ask(bid, spread=spread, point=point_size)[1] if spread > 0 else ask
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
        bid, ask = mt5_bid_ask(bar.open, spread=bar.spread, point=point_size)
        return bid, ask, bar.spread, point_size
    return bar.close, bar.close, 0, 0.0


def resolve_eval_quote(
    bar: object, *, basis: str, point_size: float
) -> "tuple[float, float]":
    """含み損益評価の (bid, ask) を評価基準（floating_pnl_basis）から解決する（🟡-10b）。

    Account に埋め込まれていた執行クォート規約（旧 Account._eval_price）を usecase 側へ
    移送したもの（ISSUE-094 🟡-10b: 最内 Entity への執行クォート規約漏出の是正）。
    含み損益は決済価格基準で評価し、買い保有は Bid=bar.close、売り保有は Ask で評価する:
        basis="close"（既定・後方互換）: Ask=bar.close（spread 無視＝従来不変）。
        basis="bid_ask": Ask=bar.close + bar.spread×point_size（決済価格基準・売り悲観化）。
    解決した (bid, ask) は Account.update_floating_pnl_at（買い=bid / 売り=ask）へ渡す。
    close 基準では bid==ask=bar.close ゆえ買い・売りとも close 評価で従来と完全一致する。
    """
    if basis == "bid_ask":
        return mt5_bid_ask(bar.close, spread=bar.spread, point=point_size)
    return bar.close, bar.close


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


#: ペンディング種別 → トリガ条件（実 MT5・約定価格＝注文価格・スリッページ 0）。
#:
#: トリガ条件の**唯一の宣言**である。種別を増やす作業は行の追加で閉じ、if/elif の
#: 順序という無関係な性質を持ち込まない（OCP）。種別の文字列がこの表の外に現れて
#: いないことは `test_pending_trigger_table.py` が構文木で固定する。
PENDING_TRIGGERS: dict[str, Callable[[float, float, float], bool]] = {
    "buy_limit":  lambda bid, ask, price: ask <= price,   # 現値が指値まで下落して買い
    "sell_limit": lambda bid, ask, price: bid >= price,   # 現値が指値まで上昇して売り
    "buy_stop":   lambda bid, ask, price: ask >= price,   # 現値が逆指値まで上昇して買い
    "sell_stop":  lambda bid, ask, price: bid <= price,   # 現値が逆指値まで下落して売り
}


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
    trigger = PENDING_TRIGGERS.get(order.kind)
    if trigger is None:          # 表に無い種別（成行など）は約定しない。
        return None
    if not trigger(bid, ask, price):
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
