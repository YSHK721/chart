"""usecase/_execution.py 純粋ヘルパーの単体テスト（PROCESS §4・§5）。

Interactor が呼ぶ約定計算（成行・BuyLimit 指値・SL/TP ヒット判定）を
domain エンティティのみ操作する純粋関数として固定する。外部 I/O・pandas を持たない。

検証観点:
    * 成行約定（§4.1）: 買い=Ask 約定・売り=Bid 約定で Position を生成する。
    * BuyLimit 指値（§4.2）: Ask<=limit で約定・time>=expire で失効。
    * SL/TP ヒット（§5）: 買い low<=sl/high>=tp・売り high>=sl/low<=tp、
      同足両ヒットは SL 優先（決定論 #3・config.sltp_tie）。
"""
from __future__ import annotations

from simulator.domain.order import Order
from simulator.domain.position import Position
from simulator.usecase._execution import (
    check_sltp_hit,
    check_sltp_hit_at_tick,
    fill_buy_limit,
    fill_market_order,
    fill_pending_order,
)


# ---- B1: 成行約定（PROCESS §4.1） ----

class TestFillMarketOrder:
    def test_buy_market_fills_at_ask_price(self):
        # Arrange: 買い成行注文（kind=market）と現ティックの bid/ask
        order = Order(side="buy", kind="market", volume=1.0, price=None)
        # Act: 成行約定（買いは Ask で約定）
        position = fill_market_order(order, bid=1.0998, ask=1.1002)
        # Assert: entry_price は Ask、side/volume は注文どおり
        assert isinstance(position, Position)
        assert position.side == "buy"
        assert position.entry_price == 1.1002
        assert position.volume == 1.0

    def test_sell_market_fills_at_bid_price(self):
        # Arrange: 売り成行注文
        order = Order(side="sell", kind="market", volume=2.0, price=None)
        # Act: 売りは Bid で約定
        position = fill_market_order(order, bid=1.0998, ask=1.1002)
        # Assert
        assert position.side == "sell"
        assert position.entry_price == 1.0998
        assert position.volume == 2.0


# ---- B2: BuyLimit 指値約定・失効（PROCESS §4.2） ----

class TestFillBuyLimit:
    def test_fills_at_limit_price_when_ask_reaches_limit(self):
        # Arrange: BuyLimit 指値=1.1000、期限 t=100。現ティック ask が指値以下に到達
        order = Order(side="buy", kind="buy_limit", volume=1.0, price=1.1000)
        # Act: Ask <= limit に到達したティック（約定価格は指値・スリッページ0）
        position = fill_buy_limit(order, ask=1.0999, tick_time=50, expire_time=100)
        # Assert: 指値価格で約定した Position（失効ではない）
        assert isinstance(position, Position)
        assert position.side == "buy"
        assert position.entry_price == 1.1000

    def test_returns_none_when_ask_above_limit_and_not_expired(self):
        # Arrange: Ask が指値より高い（未到達）かつ期限内
        order = Order(side="buy", kind="buy_limit", volume=1.0, price=1.1000)
        # Act
        position = fill_buy_limit(order, ask=1.1005, tick_time=50, expire_time=100)
        # Assert: 約定せず保留継続（None）
        assert position is None

    def test_expires_when_tick_time_reaches_expire_even_if_reachable(self):
        # Arrange: 期限到達ティック（tick_time >= expire_time）。失効を約定に優先する
        order = Order(side="buy", kind="buy_limit", volume=1.0, price=1.1000)
        # Act: Ask は指値以下だが期限到達 → 失効（PROCESS §4.2 b は失効を規定）
        result = fill_buy_limit(order, ask=1.0999, tick_time=100, expire_time=100)
        # Assert: 失効を表す "expired" を返す（None=保留 と区別する）
        assert result == "expired"


# ---- B2b: ペンディング 4 種のトリガ約定（PROCESS §4.2 拡張・実 MT5 突合） ----

class TestFillPendingOrder:
    """指値/逆指値 4 種のトリガ条件（約定価格＝注文価格・スリッページ 0）。"""

    def test_buy_limit_fills_when_ask_at_or_below_price(self):
        order = Order(side="buy", kind="buy_limit", volume=1.0, price=100.0)
        pos = fill_pending_order(order, bid=99.5, ask=100.0)  # ask==price 境界
        assert isinstance(pos, Position)
        assert pos.side == "buy" and pos.entry_price == 100.0

    def test_buy_limit_pending_when_ask_above_price(self):
        order = Order(side="buy", kind="buy_limit", volume=1.0, price=100.0)
        assert fill_pending_order(order, bid=100.05, ask=100.1) is None

    def test_sell_limit_fills_when_bid_at_or_above_price(self):
        order = Order(side="sell", kind="sell_limit", volume=1.0, price=100.0)
        pos = fill_pending_order(order, bid=100.0, ask=100.5)  # bid==price 境界
        assert isinstance(pos, Position)
        assert pos.side == "sell" and pos.entry_price == 100.0

    def test_sell_limit_pending_when_bid_below_price(self):
        order = Order(side="sell", kind="sell_limit", volume=1.0, price=100.0)
        assert fill_pending_order(order, bid=99.9, ask=100.4) is None

    def test_buy_stop_fills_when_ask_at_or_above_price(self):
        order = Order(side="buy", kind="buy_stop", volume=1.0, price=100.0)
        pos = fill_pending_order(order, bid=99.5, ask=100.0)
        assert isinstance(pos, Position)
        assert pos.side == "buy" and pos.entry_price == 100.0

    def test_buy_stop_pending_when_ask_below_price(self):
        order = Order(side="buy", kind="buy_stop", volume=1.0, price=100.0)
        assert fill_pending_order(order, bid=99.4, ask=99.9) is None

    def test_sell_stop_fills_when_bid_at_or_below_price(self):
        order = Order(side="sell", kind="sell_stop", volume=1.0, price=100.0)
        pos = fill_pending_order(order, bid=100.0, ask=100.5)
        assert isinstance(pos, Position)
        assert pos.side == "sell" and pos.entry_price == 100.0

    def test_sell_stop_pending_when_bid_above_price(self):
        order = Order(side="sell", kind="sell_stop", volume=1.0, price=100.0)
        assert fill_pending_order(order, bid=100.1, ask=100.6) is None


# ---- B1b: スプレッド対応の成行約定（実 MT5 突合・後方互換） ----

class TestFillMarketOrderWithSpread:
    """買い=bid+spread×point / 売り=bid。spread=0（既定）で従来と完全同一。

    実 MT5 アンカー（fixture report_900005560.json deal2）:
        初回 buy@01:01 fill=39412.0 ＝ そのバー open(bid)=39402.0 + spread100×point0.1=10。
        売りは bid(=39402.0) で約定。JP225: point_size=0.1。
    """

    def test_buy_with_spread_fills_at_bid_plus_spread_times_point(self):
        # Arrange: 実 MT5 初回約定（bid=open=39402.0, spread=100, point=0.1）
        order = Order(side="buy", kind="market", volume=1.0, price=None)
        # Act: 買い = bid + spread×point = 39402.0 + 100×0.1 = 39412.0
        position = fill_market_order(
            order, bid=39402.0, ask=39402.0, spread=100, point_size=0.1
        )
        # Assert: 実 MT5 初回 buy fill=39412.0 と一致
        assert position.side == "buy"
        assert position.entry_price == 39412.0

    def test_sell_with_spread_fills_at_bid(self):
        # Arrange: 同バー（bid=open=39402.0, spread=100, point=0.1）
        order = Order(side="sell", kind="market", volume=1.0, price=None)
        # Act: 売りは bid で約定（spread はかけない）
        position = fill_market_order(
            order, bid=39402.0, ask=39402.0, spread=100, point_size=0.1
        )
        # Assert: 売り fill = bid = 39402.0
        assert position.side == "sell"
        assert position.entry_price == 39402.0

    def test_spread_zero_buy_matches_legacy_ask_fill(self):
        # Arrange: spread 未指定（既定 0）は従来挙動（買い=Ask）と完全同一であること
        order = Order(side="buy", kind="market", volume=1.0, price=None)
        # Act: spread を渡さない → 従来の buy=ask 経路
        position = fill_market_order(order, bid=1.0998, ask=1.1002)
        # Assert: 既存 test_buy_market_fills_at_ask_price と同値（後方互換）
        assert position.entry_price == 1.1002

    def test_spread_zero_explicit_buy_matches_legacy_ask_fill(self):
        # Arrange: spread=0 を明示しても従来挙動（買い=Ask）であること
        order = Order(side="buy", kind="market", volume=1.0, price=None)
        # Act: spread=0 明示
        position = fill_market_order(
            order, bid=1.0998, ask=1.1002, spread=0, point_size=0.0001
        )
        # Assert: spread=0 は ask 経路に等しい（bid+0×point=bid だが従来は ask を採用）
        assert position.entry_price == 1.1002


# ---- B3: SL/TP ヒット判定（PROCESS §5・決定論 #3 SL 優先） ----

class TestCheckSltpHit:
    def test_buy_sl_hit_when_low_reaches_sl(self):
        # Arrange: 買いポジ、SL=1.0950 / TP=1.1100。足 low が SL を貫く
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        # Act
        reason = check_sltp_hit(
            position, high=1.1010, low=1.0940, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        # Assert: SL ヒット
        assert reason == "sl"

    def test_buy_tp_hit_when_high_reaches_tp(self):
        # Arrange: 買いポジ。足 high が TP を貫く（SL は未到達）
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        # Act
        reason = check_sltp_hit(
            position, high=1.1110, low=1.0990, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        # Assert: TP ヒット
        assert reason == "tp"

    def test_sell_sl_hit_when_high_reaches_sl(self):
        # Arrange: 売りポジ、SL=1.1050（上）/ TP=1.0900（下）。足 high が SL を貫く
        position = Position(side="sell", volume=1.0, entry_price=1.1000)
        # Act
        reason = check_sltp_hit(
            position, high=1.1060, low=1.0990, sl=1.1050, tp=1.0900, sltp_tie="sl"
        )
        # Assert: SL ヒット
        assert reason == "sl"

    def test_sell_tp_hit_when_low_reaches_tp(self):
        # Arrange: 売りポジ。足 low が TP を貫く（SL 未到達）
        position = Position(side="sell", volume=1.0, entry_price=1.1000)
        # Act
        reason = check_sltp_hit(
            position, high=1.1010, low=1.0890, sl=1.1050, tp=1.0900, sltp_tie="sl"
        )
        # Assert: TP ヒット
        assert reason == "tp"

    def test_no_hit_returns_none_when_bar_within_sl_tp(self):
        # Arrange: 足が SL/TP どちらにも到達しない
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        # Act
        reason = check_sltp_hit(
            position, high=1.1010, low=1.0990, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        # Assert: ヒットなし
        assert reason is None

    def test_both_hit_same_bar_prefers_sl(self):
        # Arrange: 買いポジ。同足で low<=sl かつ high>=tp（両方到達）
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        # Act: 決定論 #3 SL 優先（保守側）
        reason = check_sltp_hit(
            position, high=1.1110, low=1.0940, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        # Assert: 両ヒット時は SL を返す
        assert reason == "sl"


# ---- B3b: 単一ティック価格による SL/TP ヒット判定（every-tick #2・一般化） ----
# 既存 check_sltp_hit は bar の high/low で判定する。every-tick では 1 ティック＝1 価格
# のため、単一ティック価格 1 点で判定する check_sltp_hit_at_tick を新設する（既存経路は不変）。
# 単一価格 p は high=low=p に相当し、bar 版の決定論ロジック（sltp_tie）を継承する。


class TestCheckSltpHitAtTick:
    def test_buy_sl_hit_when_tick_price_at_or_below_sl(self):
        # 買い: 到達ティック price<=sl で SL ヒット。
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        reason = check_sltp_hit_at_tick(
            position, price=1.0950, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        assert reason == "sl"

    def test_buy_tp_hit_when_tick_price_at_or_above_tp(self):
        # 買い: 到達ティック price>=tp で TP ヒット（SL 未到達）。
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        reason = check_sltp_hit_at_tick(
            position, price=1.1100, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        assert reason == "tp"

    def test_sell_sl_hit_when_tick_price_at_or_above_sl(self):
        # 売り: 到達ティック price>=sl で SL ヒット。
        position = Position(side="sell", volume=1.0, entry_price=1.1000)
        reason = check_sltp_hit_at_tick(
            position, price=1.1050, sl=1.1050, tp=1.0900, sltp_tie="sl"
        )
        assert reason == "sl"

    def test_sell_tp_hit_when_tick_price_at_or_below_tp(self):
        # 売り: 到達ティック price<=tp で TP ヒット（SL 未到達）。
        position = Position(side="sell", volume=1.0, entry_price=1.1000)
        reason = check_sltp_hit_at_tick(
            position, price=1.0900, sl=1.1050, tp=1.0900, sltp_tie="sl"
        )
        assert reason == "tp"

    def test_no_hit_returns_none_when_tick_within_sl_tp(self):
        # ティック価格が SL/TP どちらにも到達しない。
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        reason = check_sltp_hit_at_tick(
            position, price=1.1000, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        assert reason is None

    def test_backward_compat_equals_bar_version_with_price_as_high_low(self):
        # 後方互換性の橋渡し: 単一ティック価格 p は bar 版に high=low=p を渡した結果と一致する
        # （spread=0・既存経路と完全整合）。買いで price=sl の SL ヒット境界を例に固定する。
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        tick_reason = check_sltp_hit_at_tick(
            position, price=1.0950, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        bar_reason = check_sltp_hit(
            position, high=1.0950, low=1.0950, sl=1.0950, tp=1.1100, sltp_tie="sl"
        )
        assert tick_reason == bar_reason == "sl"

    def test_both_hit_same_tick_prefers_sl(self):
        # 単一ティックで SL/TP 両到達は理論上 sl==tp==price のみ。決定論 #3 SL 優先を継承。
        position = Position(side="buy", volume=1.0, entry_price=1.1000)
        reason = check_sltp_hit_at_tick(
            position, price=1.1000, sl=1.1000, tp=1.1000, sltp_tie="sl"
        )
        assert reason == "sl"
