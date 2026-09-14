"""A-SizingDecorator: 戦略・エンジン双方を無改変とする発注量差し替え（基本設計書 §3.5.5 案 x）。

構造（Decorator パターン・OCP / LSP）:
    既存 `StrategyPort` 実装を**別の `StrategyPort` 実装で包んで DI する**。engine
    （`run_backtest.py`）が呼ぶのは on_init / on_new_bar / on_tick / on_position_check の
    4 点であり、本クラスは 4 点すべてを内側の戦略へ委譲する（LSP）。戦略 6 本・
    `run_backtest.py`・`usecase/ports.py` はいずれも 1 行も変わらない（§12.3-2）。

権威（§12.1 依頼者裁定）:
    ON のとき `SizingPort` が発注量の**唯一の権威**であり、戦略が返した `volume` を
    一律で上書きする。自前サイジングを持つ `weekly_vol_band`（`domain/volatility_band.py`）も
    特別扱いしない。**戦略名による分岐は持たない**（§12.1「戦略リストのハードコード禁止」）。

推定建値（§3.5.5 実証 6・7／§12.2）:
    成行       — 実際の建値はエンジン内部の `derive_quotes` が決め Decorator へ渡らない。
                 したがって指標レジストリの価格系列から**推定**する。系列名は
                 `sizing_ports.required_price_series(entry_price_basis)` が決める。
    ペンディング — `order.price` が確定済みなので推定しない（差が生じない）。
    ティック    — 引数の bid/ask（買い=ask / 売り=bid）を使うので差が生じない。

`simulator.domain.order.Order` は frozen dataclass のため、属性の書換ではなく `dataclasses.replace`
による**同値の再構築**とする（§3.5.5 実証 5）。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from simulator.adapter.sizing.account_margin_sizing import AccountMarginSizing
from simulator.domain.order import Order
from simulator.usecase.ports import StrategyPort
from simulator.usecase.sizing_models import (
    BLOCK_NO_RISK_DISTANCE,
    SizingConfig,
    SizingContext,
    SizingRule,
)
from simulator.usecase.sizing_ports import (
    SizingPort,
    SizingRequiresStopLossError,
    required_price_series,
)

# 成行の kind（これ以外は price 確定済みのペンディング）。
_MARKET = "market"


class SizingDecorator(StrategyPort):
    """内側の戦略が返した発注の量を `SizingPort` の決定で差し替える。

    ``price_series``: 成行の推定建値を取る指標系列名（"close" / "open"）。
    """

    def __init__(
        self, inner: StrategyPort, sizing: SizingPort, *, price_series: str
    ) -> None:
        self._inner = inner
        self._sizing = sizing
        self._price_series = price_series

    # ---- StrategyPort（4 点すべてを透過・LSP）----

    def on_init(self, config: Any, indicators: Any) -> None:
        self._inner.on_init(config, indicators)

    def on_position_check(self, position: Any, bar_index: int, indicators: Any) -> str:
        # 決済判断は戦略の責務。ここで変えると戦略の規則が壊れる（透過必須）。
        return self._inner.on_position_check(position, bar_index, indicators)

    def on_new_bar(self, bar_index: int, indicators: Any, account: Any) -> "list[Order]":
        orders = self._inner.on_new_bar(bar_index, indicators, account)
        if not orders:
            return orders
        # 成行の推定建値はバー単位で 1 回だけ引く（発注ごとに系列を引かない）。
        estimated = None
        if any(o.kind == _MARKET for o in orders):
            estimated = self._series_price(indicators, bar_index)
        return self._resize(orders, account, market_price=lambda _o: estimated)

    def on_tick(
        self, bar_index: int, bid: float, ask: float, account: Any
    ) -> "list[Order]":
        orders = self._inner.on_tick(bar_index, bid, ask, account)
        if not orders:
            return orders
        # ティック経路は約定クォートが引数で渡る（買い=ask / 売り=bid）。
        return self._resize(
            orders, account, market_price=lambda o: ask if o.side == "buy" else bid
        )

    # ---- 内部 ----

    def _series_price(self, indicators: Any, bar_index: int) -> float:
        """指標レジストリの価格系列から推定建値を引く。

        系列が無い場合は例外を伝播させる（§12.5 の受付時拒否をすり抜けた場合の
        最後の砦。無音で誤った建値を使うと発注量が静かに間違う）。
        """
        return float(indicators.get(self._price_series).iloc[bar_index])

    def _resize(self, orders: "list[Order]", account: Any, *, market_price) -> "list[Order]":
        resized: "list[Order]" = []
        equity = float(account.equity)
        for order in orders:
            entry = market_price(order) if order.kind == _MARKET else order.price
            if entry is None:
                # 成行でも系列でもない＝建値が決まらない。無音で通さない。
                continue
            decision = self._sizing.decide_volume(
                SizingContext(
                    side=order.side,
                    estimated_entry_price=float(entry),
                    stop_loss_price=order.sl,
                    equity=equity,
                )
            )
            if decision.blocked == BLOCK_NO_RISK_DISTANCE:
                # 依頼者裁定（2026-08-11）: SL が無い／SL==建値でリスク距離が決まらない
                # 発注は**黙って捨てない**。捨てると exit=0・取引 0 件の「正常終了」に
                # 化け、利用者からは「戦略が一度も発注しなかった」としか見えない。
                raise SizingRequiresStopLossError(
                    "サイジング ON の発注には SL が必須です（リスク距離が確定できません）: "
                    f"side={order.side} kind={order.kind} sl={order.sl} "
                    f"推定建値={float(entry)} — {decision.reason}"
                )
            if decision.volume is None:
                # 量を決められない発注は落とす（元の量で通すと唯一の権威が破れる）。
                continue
            resized.append(replace(order, volume=decision.volume))
        return resized


def build_sizing_decorator(
    config: SizingConfig, *, symbol_spec: Any, entry_price_basis: str
) -> "Callable[[StrategyPort], StrategyPort] | None":
    """設定から `build_interactor(strategy_decorator=...)` へ渡す関数を組み立てる。

    OFF（既定・§12.1）のときは **None** を返す。`build_interactor` は None なら戦略を
    素通しするため、既存挙動と byte 等価になる（E-2・§12.4）。

    エッジ（破産確率制約 f）の解は重い MC なので、`AccountMarginSizing` を**ここで 1 個だけ**
    構築して包む関数に閉じ込める。戦略を包み直しても f は再計算されない。

    ``entry_price_basis`` が未知なら `required_price_series` が例外を送出する
    （無音で "close" へ倒すと誤った建値で量を決める）。
    """
    if not config.enabled:
        return None
    price_series = required_price_series(entry_price_basis)
    rule = SizingRule(
        edge=config.to_edge_spec(),
        margin_rate=config.margin_rate,
        point_value=config.point_value,
        volume_min=symbol_spec.volume_min,
        volume_max=symbol_spec.volume_max,
        volume_step=symbol_spec.volume_step,
    )
    sizing = AccountMarginSizing(rule)

    def _wrap(strategy: StrategyPort) -> StrategyPort:
        return SizingDecorator(strategy, sizing, price_series=price_series)

    return _wrap
