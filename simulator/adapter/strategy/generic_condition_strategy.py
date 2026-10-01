"""A-GenericConditionStrategy: spec 駆動の汎用条件戦略（StrategyPort 実装・Phase 6 F-8）。

TBD-11 の「entry = レベル2（比較演算＋AND 連鎖＋履歴参照）」「SL/TP = 点数固定」を、
戦略ごとの手書きコードではなく **spec（:class:`EntryConditions`）＋点数換算単一ソース**
（:func:`sltp_from_points`）で表現する汎用戦略。既存 6 戦略・run_backtest・エンジンは無改変。

ポート契約（tc24051901 / pro_fit_band と同形）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
      1. warmup ガード: bar_index < max_shift（両側条件の最大 shift）→ []
      2. held_sides で同方向重複を抑止（tc/pro_fit と同一）
      3. entry_long → 成行買い / entry_short → 成行売り（long を先に評価）
      4. Order は kind="market"・price=None（約定価格は execution が解決）・
         SL/TP は :func:`sltp_from_points`（点数固定・単一ソース）
    on_position_check → "hold"（反転決済なし・固定 SL/TP のみ）

基準価格系列（§3.5.5 実証・sizing_ports の単一ソース再利用）:
    `required_price_series` が **自分の宣言**（`entry_price_basis`）から "close"/"open" を
    決める。設定から渡す口は持たない——値の権威が 2 つになると一致の保証が消える
    （ISSUE-533）。系列が registry に無い場合は例外を伝播させる（fail-stop・無音の誤建値を
    作らない）。

DIP: domain の :class:`EntryConditions` は pandas/registry を知らない。本 adapter が
    ``sample(name, shift) = indicators.get(name).iloc[bar_index-shift]`` で橋渡しする。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.entry_conditions import EntryConditions
from simulator.domain.order import Order
from simulator.domain.sltp import sltp_from_points
from simulator.usecase.entry_price_basis import basis_for_reads
from simulator.usecase.ports import EntryPriceBasisPort, StrategyPort
from simulator.usecase.sizing_ports import required_price_series


class GenericConditionStrategy(StrategyPort, EntryPriceBasisPort):
    """spec（EntryConditions）で駆動する汎用条件戦略（固定 SL/TP）。"""

    def __init__(
        self,
        *,
        entry_long: EntryConditions,
        entry_short: EntryConditions,
    ) -> None:
        self._entry_long = entry_long
        self._entry_short = entry_short
        self._max_shift = max(entry_long.max_shift, entry_short.max_shift)
        self._config: Any = None
        self._indicators: Any = None
        # 基準価格の系列名。宣言から導ける値だが **run につき 1 回だけ**解決する
        #   （発注ごとに導き直すと、出力は正しいまま回数が発注数に比例して増える）。
        self._price_series: "str | None" = None

    @property
    def entry_price_basis(self) -> "str | None":
        """判定の瞬間を**自分の条件から**導いて名乗る（`EntryPriceBasisPort`）。

        本戦略の読む足は spec が決めるので、クラス定数では表せない。条件の
        ``(系列名, shift)`` を `basis_for_reads` に通す——規則は 1 箇所にしか置かない。
        空側（`EntryConditions` が偽）は発注しないので読まないものとして扱い、両側が
        空なら足境界で判定しない（``None``）。
        """
        return basis_for_reads(
            read
            for side in (self._entry_long, self._entry_short)
            if side
            for read in side.reads
        )

    def on_init(self, config: Any, indicators: Any) -> None:
        # SL/TP>0 を拒否しない（点数 0 は sltp_from_points が None にする）。
        self._config = config
        self._indicators = indicators
        # 宣言 -> 基準価格の系列名の解決は起動時の 1 回きり（`_build_order` で導き直さない）。
        #   宣言が無い（足境界で判定しない）ときは解決しない——発注しない戦略に系列は要らない。
        declared = self.entry_price_basis
        self._price_series = None if declared is None else required_price_series(declared)

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        if bar_index < self._max_shift:  # warmup: 過去参照が範囲外
            return []

        def sample(name: str, shift: int) -> float:
            # 系列未登録は IndicatorPort.get が例外（fail-stop・伝播させる）。
            return indicators.get(name).iloc[bar_index - shift]

        held_sides = self._held_sides(account)

        # long を先に評価（両側同時成立時の決定性）。空側は __bool__=False で無効。
        if self._entry_long and "buy" not in held_sides and self._entry_long.matches(sample):
            return [self._build_order("buy", indicators, bar_index)]
        if self._entry_short and "sell" not in held_sides and self._entry_short.matches(sample):
            return [self._build_order("sell", indicators, bar_index)]
        return []

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        return "hold"  # 反転決済なし（固定 SL/TP のみ）

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _held_sides(account: Any) -> "set[str]":
        if account is None:
            return set()
        return {p.side for p in getattr(account, "open_positions", [])}

    def _build_order(self, side: str, indicators: Any, bar_index: int) -> Order:
        cfg = self._config
        # 基準価格の系列は起動時に解決済み（`on_init`）。ここで導き直さない。
        base_price = float(indicators.get(self._price_series).iloc[bar_index])
        sl, tp = sltp_from_points(
            side,
            base_price,
            cfg["stop_loss_points"],
            cfg["take_profit_points"],
            cfg["point_size"],
        )
        return Order(
            side=side,
            kind="market",
            volume=cfg["lot_size"],
            price=None,
            sl=sl,
            tp=tp,
        )
