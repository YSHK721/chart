"""E-Account: 口座状態（CLEAN_ARCH §4 / METRICS §5.1）。

関係式:
    equity = balance + floating_pnl + swap + commission
    margin_level = equity / margin * 100   （margin == 0 のとき ∞）

公開振る舞い:
    apply_deal(deal)           確定損益を balance に反映する。
    update_floating_pnl(bar)   保有ポジションの含み損益を再評価する（基準は
                               floating_pnl_basis: "close"=close 固定 / "bid_ask"=
                               買い Bid・売り Ask）。
    margin_level() -> float    証拠金維持率を返す。

設計判断（frozen 方針からの逸脱）:
    Account は run 全体で状態遷移する集約であり、CLEAN_ARCH §4 が apply_deal /
    update_floating_pnl という状態変更振る舞いを明示している。このため値オブジェクト
    （frozen）方針の例外として可変 dataclass とする。domain は numpy のみ依存可。

apply_deal は deal.profit（METRICS §5.2 で swap/commission 込みの純額）を balance に
加算する。floating_pnl は update_floating_pnl で保有ポジションから再計算する。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from simulator.domain.deal import Deal
from simulator.domain.position import Position


@dataclass
class Account:
    balance: float
    margin: float = 0.0
    contract_size: float = 100_000.0
    floating_pnl: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    open_positions: list[Position] = field(default_factory=list)
    # 含み損益の評価基準（層2・config-gated）。既定 "close"＝従来どおり bar.close 固定で
    # 全保有を評価。"bid_ask"＝決済価格基準（買い保有=Bid=close / 売り保有=Ask=
    # close+spread×point_size）。point_size は "bid_ask" 時のみ参照する。
    floating_pnl_basis: str = "close"
    point_size: float = 0.0

    @property
    def equity(self) -> float:
        # METRICS §5.1: equity = balance + floating_pnl + swap + commission
        return self.balance + self.floating_pnl + self.swap + self.commission

    def margin_level(self) -> float:
        # METRICS §5.1: equity / margin * 100。margin == 0 は ∞ 扱い
        if self.margin == 0:
            return math.inf
        return self.equity / self.margin * 100.0

    def hedged_margin_level(self, *, leverage: float, contract_size: float) -> float:
        """hedging 口座の両建て相殺後の証拠金維持率（ISSUE-094 🔴-1）。

        反対玉を相殺し「買い計・売り計の大きい側」を実効証拠金とする（同量両建ては
        実質ノーマージン＝stop-out しない・実 MT5 hedging 整合）。実効証拠金が 0 のとき
        （保有ゼロ含む）は margin_level と同じく ∞ を返す。

        従来 RunBacktestInteractor._execute_every_tick に inline されていた実効証拠金
        算出（買い計・売り計を required_margin で合算し大きい側を採る）を、口座不変
        ルールとして Account が所有する。合算は self.open_positions を保有順で走査し、
        Position.required_margin（volume × contract_size × entry_price ÷ leverage）を
        用いる。演算順・式は inline 版と同一で挙動は byte-identical。
        """
        buy_m = sum(
            pos.required_margin(leverage, contract_size)
            for pos in self.open_positions
            if pos.side == "buy"
        )
        sell_m = sum(
            pos.required_margin(leverage, contract_size)
            for pos in self.open_positions
            if pos.side == "sell"
        )
        eff_margin = max(buy_m, sell_m)
        return self.equity / eff_margin * 100.0 if eff_margin > 0 else math.inf

    def apply_deal(self, deal: Deal) -> None:
        """確定損益（profit は METRICS §5.2 の純額）を balance に反映する。"""
        self.balance += deal.profit

    def update_floating_pnl(self, bar: Any) -> None:
        """[段階縮退] bar 評価基準で含み損益を再評価する（METRICS §5.1）。

        ISSUE-094 🟡-10b: 執行クォート規約（bid/ask basis・spread×point）による評価価格の
        解決は usecase 側（_execution.resolve_eval_quote）へ移送された。本メソッドは既存
        config 経路・test_account.py が参照するため後方互換として温存する（非推奨）。含み
        損益の合算自体は update_floating_pnl_at（口座唯一の合算実装）へ委譲し、評価価格の
        解決のみ本メソッドが bar から行う。

        floating_pnl_basis="close"（既定）: 買い・売りとも bar.close で評価（従来不変）。
        floating_pnl_basis="bid_ask"（層2）: 買い=Bid(=bar.close) / 売り=Ask(=bar.close +
            bar.spread × point_size)。実 MT5 の決済価格基準評価に整合し売り含み損が悲観化。
        """
        if self.floating_pnl_basis == "bid_ask":
            self.update_floating_pnl_at(
                bid=bar.close, ask=bar.close + bar.spread * self.point_size
            )
        else:
            self.update_floating_pnl_at(bid=bar.close, ask=bar.close)

    def update_floating_pnl_at(self, *, bid: float, ask: float) -> None:
        """現在ティックの評価価格（bid/ask）で含み損益を再評価する（every-tick #3）。

        every-tick モードでは bar.close ではなく到達ティックの bid/ask で評価する。
        買い保有は決済（売り戻し）= Bid、売り保有は決済（買い戻し）= Ask で評価する
        （実 MT5 のポジション決済価格基準評価に整合）。bar 経路の update_floating_pnl
        は不変で、本メソッドは新引数版として並存する。

        config knob 不活性（real_ticks 経路）: 本メソッドは floating_pnl_basis を一切参照
        しない（評価価格は引数の実 bid/ask に固定。買い=Bid・売り=Ask）。every-tick
        （tick_model=="real_ticks"）経路は含み損評価を本メソッド経由で行うため、
        floating_pnl_basis（"close"/"bid_ask"）config はこの経路では inert（無効）であり、
        実 bid/ask 評価で代替される。basis を参照するのは bar 経路の update_floating_pnl
        （評価価格の解決を bar から行う段階縮退シム）のみ。含み損益の合算は口座で本
        メソッドに一本化されている（bar 経路も本メソッドへ委譲する・ISSUE-094 🟡-10b）。
        """
        self.floating_pnl = sum(
            pos.floating_pnl(bid if pos.side == "buy" else ask, self.contract_size)
            for pos in self.open_positions
        )

    def mark_price(self, bar: Any, side: str) -> float:
        """[段階縮退] 保有 side の含み損益評価価格（=その時点の決済現値）を返す。

        ISSUE-094 🟡-10b: 執行クォート規約の解決は usecase 側（_execution.resolve_eval_quote
        + close_price_for）へ移送された。本メソッドは後方互換として温存する（非推奨）。
        update_floating_pnl と同一の価格を返すため、stop-out 強制決済価格に再利用すると
        「margin 割れを判定した時点の価格で決済する」整合が保たれる（実 MT5 整合）。
        買い保有=Bid(=bar.close)、売り保有=Ask（"bid_ask" 時は close+spread×point、
        "close" 時は close）。
        """
        if self.floating_pnl_basis == "bid_ask" and side == "sell":
            # 売り保有の決済 = 買い戻し = Ask = Bid(close) + spread×point。
            return bar.close + bar.spread * self.point_size
        # "close"（既定）および買い保有（Bid=close）は bar.close で評価する。
        return bar.close
