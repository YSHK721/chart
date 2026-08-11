"""A-AccountMarginSizing: 口座証拠金制約つきサイジング（SizingPort 実装・adapter）。

決定手順（基本設計書 §3.5.5・§12.2・依頼者裁定）:
    1. **破産確率制約 f** を採る（`edge_ruin.solve_edge_ruin().constrained_fraction`）。
       フルケリーは「成長最速だが攻めすぎ」（参照実装
       `integrated_position_sizing_calculator.html` Step 1 のカード説明）であり採らない。
       f はジョブ全体で不変なので**一度だけ**計算して保持する（MC は 60 格子 × sims × T の
       ループであり、発注のたびに回すとバックテストが終わらない）。
    2. リスク比から口数を逆算する: ``U = f·E / (D·V)``（D=ストップ距離・V=point_value）。
    3. **証拠金制約**で頭打ちにする: `official_required_margin` が有効証拠金を超えない最大 U。
    4. **ロスカット制約**で頭打ちにする: `official_losscut_price` がストップより手前に
       来ない最大 U。ロスカットが先に発動すると SL が意味を失う。
    5. 保守側に丸める（`domain/volume_step.floor_to_step`）。下限未満は発注しない。

権威式（C-7・§12.3-3 複製絶対禁止）:
    必要証拠金・ロスカット価格は `simulator.usecase.account_engine` の
    `official_required_margin` / `official_losscut_price` を**呼ぶ**。閉形式を解いて
    上限 U を直接書き下すと式の写しになるため、**単調性を使った二分探索で権威関数に
    判定させる**。式の権威は account_engine 側の 1 箇所に留まる。
"""
from __future__ import annotations

from simulator.domain.volume_step import floor_to_step
from simulator.usecase.account_engine import (
    official_losscut_price,
    official_required_margin,
)
from simulator.usecase.edge_ruin import solve_edge_ruin
from simulator.usecase.sizing_models import (
    BLOCK_BELOW_MINIMUM,
    BLOCK_NO_EQUITY,
    BLOCK_NO_RISK_DISTANCE,
    SizingContext,
    SizingDecision,
    SizingRule,
)
from simulator.usecase.sizing_ports import SizingNotViableError, SizingPort

# 二分探索の反復回数。上限 U の相対精度 2^-60 まで詰める（刻み丸めで吸収されるため十分）。
_BISECT_ITERS = 60


class AccountMarginSizing(SizingPort):
    """`SizingRule` に従って発注量を決める。"""

    def __init__(self, rule: SizingRule) -> None:
        self._rule = rule
        # f はジョブ全体で不変。MC は重いので構築時に 1 回だけ解く。
        self._fraction = solve_edge_ruin(rule.edge).constrained_fraction
        # 🔴-4: f<=0 は「この設定では 1 枚も建たない」ことを意味する。設定だけで決まる
        # ため**実行前**に確定させる。発注時に落とすと exit=0・取引 0 件の無音終了になる。
        if self._fraction <= 0:
            edge = rule.edge
            expected_value = edge.payoff_ratio * edge.win_rate - (1 - edge.win_rate)
            raise SizingNotViableError(
                "この設定では破産確率制約 f が 0 になり 1 枚も建たないため、"
                "サイジングを有効にしたバックテストを開始できません"
                f"（EV={expected_value:.4f} 勝率={edge.win_rate} 利益率={edge.payoff_ratio} "
                f"α={edge.alpha} 破産水準={edge.ruin_level} T={edge.horizon}）。"
                "EV<=0 のほか、EV>0 でも α が厳し過ぎる／T が長過ぎる場合に起きます"
            )

    @property
    def fraction(self) -> float:
        """採用した破産確率制約 f。"""
        return self._fraction

    def decide_volume(self, context: SizingContext) -> SizingDecision:
        rule = self._rule
        f = self._fraction

        # f<=0 は構築時に排除済み（🔴-4）。ここで再判定すると SL 判定より先に立ち、
        # fail-stop（SizingRequiresStopLossError）が到達不能になる。
        if context.stop_loss_price is None:
            return SizingDecision(
                volume=None, fraction=f,
                reason="ストップ（SL）が無くリスク距離を定義できないため発注量を決められない",
                blocked=BLOCK_NO_RISK_DISTANCE,
            )
        distance = abs(context.estimated_entry_price - context.stop_loss_price)
        if distance <= 0:
            return SizingDecision(
                volume=None, fraction=f,
                reason="ストップ（SL）と推定建値が同一でリスク距離が 0 のため発注量を決められない",
                blocked=BLOCK_NO_RISK_DISTANCE,
            )
        if context.equity <= 0:
            return SizingDecision(
                volume=None, fraction=f,
                reason="有効証拠金が 0 以下のため発注しない",
                blocked=BLOCK_NO_EQUITY,
            )

        # 2. リスク比からの口数
        raw = f * context.equity / (distance * rule.point_value)
        # 3./4. 証拠金・ロスカットの各制約で頭打ちにする（権威関数に判定させる）
        capped = min(
            raw,
            self._max_units_within_margin(context),
            self._max_units_before_losscut(context),
        )
        # 5. 保守側の丸め
        volume = floor_to_step(
            capped,
            step=rule.volume_step,
            minimum=rule.volume_min,
            maximum=rule.volume_max,
        )
        if volume is None:
            return SizingDecision(
                volume=None, fraction=f,
                reason=(
                    "算出量が volume_min 未満（保守側の丸めで発注不可）: "
                    f"raw={raw:.6f} capped={capped:.6f} min={rule.volume_min}"
                ),
                blocked=BLOCK_BELOW_MINIMUM,
            )
        return SizingDecision(volume=volume, fraction=f)

    # ---- 制約（いずれも account_engine の権威関数に判定させる）----

    def _max_units_within_margin(self, context: SizingContext) -> float:
        """必要証拠金が有効証拠金を超えない最大口数。

        `official_required_margin` は口数について単調増加なので二分探索できる。
        """
        rule = self._rule

        def within(units: float) -> bool:
            margin = official_required_margin(
                [(context.estimated_entry_price, units)],
                rule.margin_rate,
                rule.point_value,
            )
            return margin <= context.equity

        return self._bisect_max(within, rule.volume_max)

    def _max_units_before_losscut(self, context: SizingContext) -> float:
        """ロスカットがストップより手前で発動しない最大口数。

        `official_losscut_price` はロング（ショート）で口数について単調に建値へ
        近づくため、「ストップより手前でない」という述語は単調であり二分探索できる。
        """
        rule = self._rule
        long = context.side == "buy"
        direction = "long" if long else "short"
        stop = context.stop_loss_price
        assert stop is not None  # decide_volume 側で除外済み

        def safe(units: float) -> bool:
            price = official_losscut_price(
                direction,
                [(context.estimated_entry_price, units)],
                context.equity,
                rule.margin_rate,
                rule.point_value,
            )
            if price is None:
                return True
            # ストップが先に効くこと（ロスカットはストップより遠い側）。
            return price < stop if long else price > stop

        return self._bisect_max(safe, rule.volume_max)

    @staticmethod
    def _bisect_max(predicate, upper: float) -> float:
        """``predicate`` が真である最大の口数を二分探索で求める（単調性を仮定）。

        ``predicate(upper)`` が真ならそのまま ``upper`` を返す。偽なら [0, upper] を
        二分し、真側の下限を返す（**保守側**＝必ず述語を満たす値を返す）。
        """
        if upper <= 0:
            return 0.0
        if predicate(upper):
            return upper
        lo, hi = 0.0, upper
        for _ in range(_BISECT_ITERS):
            mid = (lo + hi) / 2
            if predicate(mid):
                lo = mid
            else:
                hi = mid
        return lo
