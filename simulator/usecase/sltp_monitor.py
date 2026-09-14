"""SL/TP 到達判定（`SltpMonitor`）。UC-001 の協働クラス（ISSUE-502 段階 4A）。

責務は 1 つだけである: **評価点 1 つで、どの保有玉が SL/TP に触れたかを決め、触れた玉を
帳簿へ渡す**。触れたかどうかの規則（監視外の 2 種類・サイドごとの価格範囲・同値の優先）は
すべてここに閉じ、run の進み方（どの点を評価するか）は呼出側が決める。

なぜ Interactor から出したか:
    「同値で SL と TP が同時に当たったときどちらを採るか」（`sltp_tie`）や「建てた足を
    監視するか」は、実 MT5 の server 挙動に合わせて改訂される軸である。run の進み方
    （ウォームアップ・粒度・ペンディングの持続）とは別の理由・別のタイミングで動く。
"""
from __future__ import annotations

from typing import Any

from simulator.usecase._execution import check_sltp_hit
from simulator.usecase.open_trade import OpenTrade
from simulator.usecase.trade_ledger import TradeLedger


class SltpMonitor:
    """1 run ぶんの SL/TP 監視（同値裁定の規則と帳簿への渡し口を保持する）。"""

    def __init__(self, *, ledger: TradeLedger, sltp_tie: str) -> None:
        self._ledger = ledger
        # 同値で SL と TP の双方に触れたときの優先（run のあいだ不変）。
        self._sltp_tie = sltp_tie

    def check(self, point: Any, open_trades: list, *, closed: bool) -> list:
        """評価点 1 つで保有玉の SL/TP 到達を判定し、残る保有列を返す（H 段の単一化）。

        監視しない玉が 2 種類ある:

          * 市場閉鎖バーの全玉。SL/TP は顧客注文であり、トレードセッション外では
            執行されない（stop-out はブローカーのリスク清算なので閉鎖中も続く）。
          * 建てた足の玉のうち、まだ「約定の次」に達していないもの。成行は足全体を
            監視外にし（fill_delay=次tick）、ペンディング約定は約定ティックより後から
            監視する（約定したティック自身では決済しない＝実 MT5 server 整合）。

        到達を見る価格範囲は点がサイドごとに持つ。ペンディング経路だけが買い（Bid）と
        売り（Ask）で違う範囲になるためである。
        """
        if closed:
            return open_trades
        still_open: "list[OpenTrade]" = []
        for ot in open_trades:
            if ot.opened_bar_index == point.bar_index:
                if ot.skip_entry_bar:
                    still_open.append(ot)  # 建てた足は監視外（成行）
                    continue
                if point.tick_ordinal <= ot.opened_tick_ordinal:
                    still_open.append(ot)  # 約定ティック以前は監視外（ペンディング）
                    continue
            if ot.position.side == "buy":
                hit_high, hit_low = point.hit_buy_high, point.hit_buy_low
            else:
                hit_high, hit_low = point.hit_sell_high, point.hit_sell_low
            reason = check_sltp_hit(
                ot.position,
                high=hit_high,
                low=hit_low,
                sl=ot.sl,
                tp=ot.tp,
                sltp_tie=self._sltp_tie,
            )
            if reason is None:
                still_open.append(ot)
                continue
            exit_price = ot.sl if reason == "sl" else ot.tp
            self._ledger.close(
                ot,
                exit_time=point.bar.time,
                exit_price=exit_price,
                exit_reason=reason,
            )
        return still_open
