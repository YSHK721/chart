"""約定執行（`OrderExecutor`）。UC-001 の協働クラス（ISSUE-502 段階 4A）。

責務は 1 つだけである: **注文を玉に変え、口座へ反映する**。成行の約定・反対玉の reverse
決済・ペンディングの設置とトリガ約定・テスト終了時の清算は、いずれも「注文（または保有）
を約定価格で口座へ書き込む」という同じ動機で改訂される。

なぜ Interactor から出したか:
    約定の規則（建値クォートの取り方・走査順＝反映順・OCO・約定ティックの序数）は実 MT5
    の server 挙動に合わせて改訂される軸であり、run の進み方（どのバーを取引区間とするか・
    どの点を評価するか）とは別の理由で動く。

残存ペンディング（`resting_pending`）の所有:
    未約定のまま次足へ持ち越す注文列は、**設置・トリガ・持ち越し・貼り替え・再アーム**の
    すべてが本クラスの手続きである。run 側に置くと 5 箇所が同じ列を直接書き換える形になり、
    「貼り替えたつもりで消し忘れる」類の食い違いを型で防げない。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.account import Account
from simulator.usecase._execution import (
    close_price_for,
    derive_quotes,
    fill_market_order,
)
from simulator.usecase.open_trade import OpenTrade
from simulator.usecase.pending_lifecycle import PendingLifecycleEngine
from simulator.usecase.trade_ledger import TradeLedger


class OrderExecutor:
    """1 run ぶんの約定執行（口座・帳簿・銘柄仕様・約定の設定を保持する）。"""

    def __init__(
        self,
        *,
        account: Account,
        ledger: TradeLedger,
        spec: Any,
        leverage: float,
        contract_size: float,
        entry_price_basis: str,
        pending_oco: bool,
    ) -> None:
        self._account = account
        self._ledger = ledger
        self._spec = spec
        self._leverage = leverage
        self._contract_size = contract_size
        # 建値クォートの基準（run のあいだ不変）。
        self._entry_price_basis = entry_price_basis
        # 同一評価点で複数トリガしたときに 1 本だけ約定させるか（run のあいだ不変）。
        self._pending_oco = pending_oco
        # 残存ペンディング（指値/逆指値）。前足で設置され未約定のまま次足へ持ち越し、
        # 評価点ごとにトリガ評価される。ペンディング経路以外では常に空で挙動不変。
        self._resting: list = []

    # ---- 残存ペンディングの出し入れ（所有者は本クラス） ----

    def has_resting_pending(self) -> bool:
        """未約定のまま持ち越している注文が在るか。"""
        return bool(self._resting)

    def clear_resting_pending(self) -> None:
        """残存ペンディングを取り消す（EA が毎バー貼り替える非持続モードの取消段）。"""
        self._resting = []

    def rearm(self, orders: Any) -> None:
        """残存ペンディングを与えられた注文列で置き換える（持続モードの足途中再アーム）。"""
        self._resting = list(orders)

    # ---- 成行 ----

    def close_opposite(
        self, open_trades: list, order: Any, *, bar: Any, bid: float, ask: float
    ) -> list:
        """発注と反対サイドの保有玉を reverse 決済し、残る保有列を返す（PROCESS §6）。

        走査順をそのまま残す（残す玉は `kept` へ現れた順に積む）。保有列の並びは証拠金の
        按分解放と強制決済の走査順に効くため、ここで並びが変わると確定トレードの並びが動く。

        決済価格は `close_price_for` が約定価格ルール（買い決済=Bid / 売り決済=Ask）で
        一意に決める。成行・ペンディング設置の双方が同じ規則で反対玉を畳む。
        """
        kept: "list[OpenTrade]" = []
        for ot in open_trades:
            if ot.position.side != order.side:
                close_price = close_price_for(ot.position.side, bid=bid, ask=ask)
                self._ledger.close(
                    ot,
                    exit_time=bar.time,
                    exit_price=close_price,
                    exit_reason="reverse",
                )
            else:
                kept.append(ot)
        return kept

    def fill_market(
        self, orders: list, open_trades: list, *, bar: Any, bar_index: int
    ) -> list:
        """成行注文を約定させる（両実行経路で完全一致していた F 段の単一化）。

        1 注文ごとに「反対玉の reverse 決済 → 建玉 → 口座反映」を**完了してから**次の
        注文へ進む。まとめて約定してから反映すると、2 本目の注文が 1 本目の建玉を
        見られず、起きるべき reverse 決済が起きなくなる（走査順＝反映順）。

        建値は足境界のバー open クォート（`derive_quotes`）で、両経路とも同一である
        （実 MT5 は新規バーの成行をバー open のクォートで約定する）。注文が 1 本も無い
        バーではクォートを引かない——引いても捨てるだけの計算だからである。

        事後条件: 更新後の保有列を返す（呼出側が受け取って進む）。
        """
        if not orders:
            return open_trades
        bid, ask, fill_spread, fill_point = derive_quotes(
            bar,
            entry_price_basis=self._entry_price_basis,
            point_size=self._spec.point_size,
        )
        for order in orders:
            open_trades = self.close_opposite(open_trades, order, bar=bar, bid=bid, ask=ask)
            position = fill_market_order(
                order, bid=bid, ask=ask, spread=fill_spread, point_size=fill_point
            )
            self._account.open_positions.append(position)
            self._account.margin += position.required_margin(
                self._leverage, self._contract_size
            )
            open_trades.append(
                OpenTrade(
                    position=position,
                    sl=order.sl,
                    tp=order.tp,
                    entry_time=bar.time,
                    entry_price=position.entry_price,
                    opened_bar_index=bar_index,
                )
            )
        return open_trades

    # ---- ペンディング ----

    def place_pending(self, open_trades: list, orders: list, *, bar: Any) -> list:
        """ペンディング（指値/逆指値）を設置し、残る保有列を返す（PROCESS §4.2 拡張）。

        実 EA は毎バー自ペンディングを取消し、最新シグナルで再設置する。逆方向を保有して
        いれば bar open クォートで成行ドテン決済してから設置する（原典
        PositionClose→OpenPending）。クォートは注文が 1 本以上あるときだけ引く。
        """
        pbid0, pask0, _, _ = derive_quotes(
            bar,
            entry_price_basis=self._entry_price_basis,
            point_size=self._spec.point_size,
        )
        for order in orders:
            open_trades = self.close_opposite(
                open_trades, order, bar=bar, bid=pbid0, ask=pask0
            )
            self._resting.append(order)
        return open_trades

    def trigger_resting(self, open_trades: list, point: Any) -> None:
        """残存ペンディングを当該評価点のクォートでトリガ評価する。

        OCO: 同一評価点で trigger した注文は（OCO 無効なら）すべて約定する。実 MT5 の
        hedging 口座では広い spread や doji で両建てが成立する（2604-02 実証）。約定が
        起きたら、非約定分は EA が取り消す。
        """
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            self._resting,
            bid=point.eval_bid,
            ask=point.eval_ask,
            oco=self._pending_oco,
        )
        self._admit_pending_fills(
            open_trades,
            filled,
            bar=point.bar,
            bar_index=point.bar_index,
            tick_ordinal=point.tick_ordinal,
        )
        self._resting = carried

    def _admit_pending_fills(
        self,
        open_trades: list,
        filled: list,
        *,
        bar: Any,
        bar_index: int,
        tick_ordinal: int,
    ) -> None:
        """トリガしたペンディングの約定を口座へ反映する（走査順＝反映順）。

        トリガ判定と OCO はエンジン（PendingLifecycleEngine）が純ロジックとして持ち、
        約定 Position の口座反映（保有列・証拠金・保有玉）は口座を触る本クラスが担う。
        約定玉は `skip_entry_bar=False`・`opened_tick_ordinal` 付きで積み、「約定した
        ティックより後」からのみ SL/TP を監視させる（約定したティック自身では決済しない
        ＝実 MT5 server 整合）。
        """
        for order, pos in filled:
            self._account.open_positions.append(pos)
            self._account.margin += pos.required_margin(
                self._leverage, self._contract_size
            )
            open_trades.append(
                OpenTrade(
                    position=pos,
                    sl=order.sl,
                    tp=order.tp,
                    entry_time=bar.time,
                    entry_price=pos.entry_price,
                    opened_bar_index=bar_index,
                    skip_entry_bar=False,
                    opened_tick_ordinal=tick_ordinal,
                )
            )

    # ---- テスト終了時の清算 ----

    def close_all_at_final_bar(self, open_trades: list, final_bar: Any) -> list:
        """テスト期間終了時に残る建玉を最終足の close クォートで清算し、空の保有列を返す。

        実 MT5 はテスト終了時に未決済ポジションを最終価格で決済する（2603-01: 最終 buy を
        最終足 23:59 の close=51029.8 で決済し profit+20）。買い決済=Bid=close /
        売り決済=Ask=close+spread×point。
        """
        f_bid = final_bar.close
        f_ask = final_bar.close + final_bar.spread * self._spec.point_size
        for ot in open_trades:
            close_price = close_price_for(ot.position.side, bid=f_bid, ask=f_ask)
            self._ledger.close(
                ot,
                exit_time=final_bar.time,
                exit_price=close_price,
                exit_reason="end_of_test",
            )
        return []
