"""証拠金の再評価と強制決済（`MarginGuard`）。UC-001 の協働クラス（ISSUE-502 段階 4A）。

責務は 1 つだけである: **評価点 1 つで口座を値洗いし、維持率が水準を割ったら run を捨てる
か全玉を畳む**。equity 系列への記録は値洗いと同じ 1 手続きの産物であり、切り離すと
「評価点 1 つにつき equity ちょうど 1 点」という対応（equity 系 stats の土台）を型で
保てなくなるため本クラスが持つ。

なぜ Interactor から出したか:
    証拠金の規則（両建て相殺・stop-out 水準・割れたときの方針・強制決済価格）はブローカー
    仕様に合わせて改訂される軸であり、run の進み方とは別の理由・別のタイミングで動く。

評価点は 2 種類ある（どちらも本クラスが持つ）:
    * バー open の pseudo-tick（バー粒度のみ・`settle_bar_open`）。週末ギャップ等で open が
      割れた玉を「バー open クォート」で畳む。
    * 通常の評価点（`settle`）。粒度を問わず全評価点で通る。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.account import Account
from simulator.domain.exceptions import MarginCallError
from simulator.usecase._execution import (
    close_price_for,
    derive_quotes,
    resolve_eval_quote,
)
from simulator.usecase.stop_out_policy import StopOutContext
from simulator.usecase.trade_ledger import TradeLedger

# 証拠金割れで run を捨てるときの文言。評価点の名前（_STOP_OUT_AT_*）を末尾に足す。
_STOP_OUT_BREACH_MESSAGE = "margin_level が stop_out_level を下回りました"
# 評価点の名前。バー open 評価だけは「どの pseudo-tick で割れたか」が診断に要るため
# 文言に残す（移設前と byte 一致）。他の 2 点は評価点名を付けない。
_STOP_OUT_AT_BAR_OPEN = "（bar open 評価）"
_STOP_OUT_AT_EVALUATION = ""

# バー open の pseudo-tick を評価するときの約定価格基準（実 bid/ask 固定）。
_BAR_OPEN_PSEUDO_TICK_BASIS = "current_open"


class MarginGuard:
    """1 run ぶんの証拠金監視（口座・帳簿・方針・水準・評価クォートの基準を保持する）。"""

    def __init__(
        self,
        *,
        account: Account,
        ledger: TradeLedger,
        policy: Any,
        stop_out_level: float,
        leverage: float,
        contract_size: float,
        equity_curve: list,
        floating_pnl_basis: str,
        point_size: float,
    ) -> None:
        self._account = account
        self._ledger = ledger
        # 証拠金割れの方針は run につき 1 度だけ引いたものを受け取る（評価点ごとに引き直さない）。
        self._policy = policy
        self._stop_out_level = stop_out_level
        self._leverage = leverage
        self._contract_size = contract_size
        self._equity_curve = equity_curve
        self._floating_pnl_basis = floating_pnl_basis
        self._point_size = point_size

    def record_carried_quote(self, *, eval_bid: float, eval_ask: float) -> None:
        """成立価格の無いバーの点を、持ち越したクォートで値洗いして equity へ 1 点残す。

        ティックが 1 本も無いバーの点で呼ばれる。そのバーで実際に成立した価格が無いので
        SL/TP も stop-out も判定しない（判定する材料が無い）。equity 系列だけは評価点との
        1:1 対応を保つため 1 点記録する。

        equity 系列へ追記するのは本クラスだけである（`settle` と本メソッドの 2 つ）——
        「評価点 1 つにつき equity ちょうど 1 点」は equity 系 stats の土台であり、追記者が
        散れば構造からは保てなくなる。
        """
        self._account.update_floating_pnl_at(bid=eval_bid, ask=eval_ask)
        self._equity_curve.append(self._account.equity)

    def settle_bar_open(
        self, open_trades: list, halted: bool, *, bar: Any, bar_index: int
    ) -> "tuple[list, bool]":
        """バー open の pseudo-tick で証拠金を先行評価する（バー粒度の規則・config gated）。

        週末ギャップ等で open が割れた保有玉は「バー open クォート」で強制決済される
        （買い=Bid=open / 売り=Ask=open+spread×point）。後段の close 基準判定は残すため、
        open が割れず bar 内で割れる場合は従来どおり close で決済する（ISSUE-022）。
        open 評価は `floating_pnl_basis` を参照せず実 bid/ask 固定である。

        非 breach のときは open 基準で書き換えた含み損益を close 基準へ**戻す**（後続の
        戦略呼び出し等へ open 基準 floating を漏らさない。equity_curve は後段の通常評価点
        が close 基準で記録する）。

        事後条件: `(更新後の保有列, halt したか)`。割れて強制決済したときは保有列が空・
        halt が真になる。方針が「強制決済しない」なら返らず証拠金割れ例外を送出する。
        """
        o_bid, o_ask, _, _ = derive_quotes(
            bar,
            entry_price_basis=_BAR_OPEN_PSEUDO_TICK_BASIS,
            point_size=self._point_size,
        )
        self._account.update_floating_pnl_at(bid=o_bid, ask=o_ask)
        if self._account.margin_level() < self._stop_out_level:
            self._apply_stop_out(
                open_trades=open_trades,
                bar=bar,
                bar_index=bar_index,
                bid=o_bid,
                ask=o_ask,
                where=_STOP_OUT_AT_BAR_OPEN,
            )
            return [], True
        nb_bid, nb_ask = resolve_eval_quote(
            bar,
            basis=self._floating_pnl_basis,
            point_size=self._point_size,
        )
        self._account.update_floating_pnl_at(bid=nb_bid, ask=nb_ask)
        return open_trades, halted

    def settle(
        self,
        open_trades: list,
        halted: bool,
        *,
        bar: Any,
        bar_index: int,
        eval_bid: float,
        eval_ask: float,
        hedged: bool,
    ) -> "tuple[list, bool]":
        """1 評価点で口座を再評価する（両実行経路で同型だった I 段の単一化）。

        手順は 3 つ: 含み損益を評価クォートで更新し、equity 系列へ 1 点記録し、証拠金
        維持率が stop-out 水準を割っていれば割れの処理へ渡す。評価点 1 つにつき equity は
        ちょうど 1 点であり、この対応が equity 系 stats（系列長・最大ドローダウン）の
        土台になる。

        `hedged`（両建ての証拠金相殺）は**ティック粒度の規則**であり、呼出側が渡す。
        バー評価は設定が立っていても単純加算のままである——これは設計上の意図ではなく
        現状の契約なので、段を束ねるにあたり寄せずにそのまま残す（寄せれば数値が動く）。

        事後条件: `(更新後の保有列, halt したか)` を返す。割れて強制決済したときは
        保有列が空・halt が真になる。方針が「強制決済しない」なら本メソッドは返らず
        証拠金割れ例外を送出する（run を捨てる）。
        """
        account = self._account
        account.update_floating_pnl_at(bid=eval_bid, ask=eval_ask)
        self._equity_curve.append(account.equity)
        margin_level = account.margin_level()
        if hedged and open_trades:
            # 実効証拠金算出は口座不変ルールとして Account が所有する（ISSUE-094）。
            #   保有列は account.open_positions と open_trades が常時 lockstep のため
            #   走査対象・順序・式が inline 版と同一＝byte-identical。
            margin_level = account.hedged_margin_level(
                leverage=self._leverage, contract_size=self._contract_size
            )
        if margin_level < self._stop_out_level:
            self._apply_stop_out(
                open_trades=open_trades,
                bar=bar,
                bar_index=bar_index,
                bid=eval_bid,
                ask=eval_ask,
                where=_STOP_OUT_AT_EVALUATION,
            )
            return [], True
        return open_trades, halted

    def _apply_stop_out(
        self,
        *,
        open_trades: list,
        bar: Any,
        bar_index: int,
        bid: float,
        ask: float,
        where: str,
    ) -> None:
        """証拠金割れの処理（3 つの評価点で完全一致していた 2 分岐の単一化）。

        方針が「強制決済しない」と決めたら run を捨てる（`MarginCallError` を送出し
        部分結果を残さない）。「強制決済する」と決めたら、全保有玉を**割れを判定した
        時点の現値クォート**で決済する（呼出側が保有列を空にし halt する）。

        強制決済価格が現値である理由: 成行の建値が始値基準（`current_open`）であっても、
        過ぎ去った始値ではなく割れた時点の値で決済するのが実 MT5 の挙動である
        （ISSUE-019）。買い＝Bid / 売り＝Ask を `close_price_for` が選ぶ。

        `where` は送出文言の末尾に付く評価点の名前（移設前の文言と byte 一致させる）。
        診断値 `margin_level` は hedged 相殺を**含まない** `account.margin_level()` で
        あり、割れ判定に使う実効値とは別である（移設前と同一）。
        """
        account = self._account
        decision = self._policy.on_breach(
            StopOutContext(
                margin_level=account.margin_level(),
                stop_out_level=self._stop_out_level,
                bar_index=bar_index,
                open_trade_count=len(open_trades),
            )
        )
        if not decision.liquidate:
            raise MarginCallError(
                _STOP_OUT_BREACH_MESSAGE + where,
                context={
                    "margin_level": account.margin_level(),
                    "stop_out_level": self._stop_out_level,
                },
                bar_index=bar_index,
            )
        for ot in open_trades:
            close_price = close_price_for(ot.position.side, bid=bid, ask=ask)
            self._ledger.close(
                ot,
                exit_time=bar.time,
                exit_price=close_price,
                exit_reason="stop_out",
            )
