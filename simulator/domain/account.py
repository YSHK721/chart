"""E-Account: 口座状態（CLEAN_ARCH §4 / METRICS §5.1）。

関係式:
    equity = balance + floating_pnl + swap + commission
    margin_level = equity / margin * 100   （margin == 0 のとき ∞）

公開振る舞い:
    apply_deal(deal)              確定損益を balance に反映する。
    update_floating_pnl_at(bid,ask) 与えられた評価価格で含み損益を合算する（口座唯一の
                                  合算実装）。
    update_floating_pnl(bar)      bar.close で含み損益を再評価する後方互換シム（close 固定・
                                  update_floating_pnl_at へ委譲）。
    margin_level() -> float       証拠金維持率を返す。
    hedged_margin_level(...)      両建て相殺後の証拠金維持率を返す。

ISSUE-095 項目2: 執行クォート規約（bid/ask basis・spread×point）は usecase 側
（_execution.resolve_eval_quote）へ完全移送済み。Account 内の bid/ask basis 分岐
（update_floating_pnl(bar)／旧 mark_price 内の floating_pnl_basis・point_size 依存）は
除去し、Account は「与えられた評価価格で floating を合算する」振る舞いへ一本化した。

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
    # [inert・ISSUE-095 項目2] 旧・含み損益評価基準／point スケール。執行クォート規約が
    # usecase（_execution.resolve_eval_quote）へ移送され、Account 内の bid/ask basis 分岐は
    # 除去された。本フィールドは本番 Account 構築（run_backtest が config.floating_pnl_basis
    # ／spec.point_size を kwargs で渡す：run_backtest.py:190-191, 489-490）が受けるため残置
    # するが、Account 内で読む箇所はもう無い（inert）。フィールド自体の削除は run_backtest の
    # 構築コード修正を伴う別タスク（本項目の触ってはならない領域）として申し送る。
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
        """[後方互換シム・close 固定] bar.close で含み損益を再評価する（METRICS §5.1）。

        ISSUE-095 項目2: 執行クォート規約（bid/ask basis・spread×point）による評価価格の
        解決は usecase 側（_execution.resolve_eval_quote）へ完全移送済みであり、本メソッド
        から bid/ask basis 分岐（floating_pnl_basis・point_size 依存）を除去した。買い・売り
        とも bar.close で評価する（従来の "close" 挙動で不変）。含み損益の合算自体は
        update_floating_pnl_at（口座唯一の合算実装）へ委譲し、本メソッドは bar.close を
        bid=ask として渡す薄いシムに縮退した。決済価格基準（bid_ask）評価が必要な経路は
        usecase が resolve_eval_quote で (bid, ask) を解決し update_floating_pnl_at へ直接
        渡す（本番経路）。
        """
        self.update_floating_pnl_at(bid=bar.close, ask=bar.close)

    def update_floating_pnl_at(self, *, bid: float, ask: float) -> None:
        """現在ティックの評価価格（bid/ask）で含み損益を再評価する（every-tick #3）。

        every-tick モードでは bar.close ではなく到達ティックの bid/ask で評価する。
        買い保有は決済（売り戻し）= Bid、売り保有は決済（買い戻し）= Ask で評価する
        （実 MT5 のポジション決済価格基準評価に整合）。含み損益の合算は口座でこのメソッド
        に一本化されている（後方互換シム update_floating_pnl(bar) も本メソッドへ委譲する）。

        評価価格の解決（bar/tick から (bid, ask) を導く執行クォート規約・basis 依存）は
        本メソッドの責務ではなく、呼び出し側 usecase（_execution.resolve_eval_quote）が担う。
        本メソッドは floating_pnl_basis を一切参照せず、引数の実 bid/ask で合算するのみ
        （ISSUE-095 項目2で bid/ask basis 分岐を Account から完全除去した）。
        """
        self.floating_pnl = sum(
            pos.floating_pnl(bid if pos.side == "buy" else ask, self.contract_size)
            for pos in self.open_positions
        )
