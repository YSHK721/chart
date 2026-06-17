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

from backtest.domain.order import Order
from backtest.domain.position import Position
from backtest.usecase._execution import (
    check_sltp_hit,
    fill_buy_limit,
    fill_market_order,
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
