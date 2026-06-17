"""UC-001 RunBacktestInteractor（CLEAN_ARCH §3・PROCESS §2・DESIGN §6）。

config + Bar 列から確定トレード・deals・equity/balance 系列を構築し、compute_stats
（UC-002）で BacktestStats を算出して BacktestResult を返す。OnTick A〜I の処理順を
Interactor 内に閉じる（CLEAN_ARCH §3 注記）。

依存性注入: StrategyPort / IndicatorPort / TickModelPort を constructor で受ける（DIP）。
usecase は domain のみ依存（adapter/framework/main・pydantic を import しない）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backtest.domain.account import Account
from backtest.domain.deal import Deal
from backtest.domain.exceptions import MarginCallError
from backtest.domain.trade_record import TradeRecord
from backtest.usecase._execution import check_sltp_hit, fill_market_order
from backtest.usecase.compute_stats import compute_stats
from backtest.usecase.models import BacktestResult
from backtest.usecase.ports import RunBacktestInputBoundary


def _close_deal(trade: TradeRecord) -> Deal:
    """確定 TradeRecord から決済 Deal を生成する（balance 反映用）。"""
    return Deal.from_close(
        side=trade.side,
        entry_price=trade.entry_price,
        close_price=trade.exit_price,
        volume=trade.volume,
        contract_size=trade.contract_size,
        swap=trade.swap,
        commission=trade.commission,
    )


@dataclass
class _OpenTrade:
    """run 中の保有玉（Position + SL/TP・建値時刻・建てた bar_index）。"""

    position: Any
    sl: float | None
    tp: float | None
    entry_time: Any
    entry_price: float
    opened_bar_index: int


@dataclass
class RunBacktestRequest:
    """UC-001 Input Model（CLEAN_ARCH §3: {config, data}）。

    data は domain の Bar 列（イテラブル）。DataFrame→Bar 変換は将来 adapter 責務。
    """

    config: Any
    bars: Any
    symbol_spec: Any
    initial_deposit: float
    stop_out_level: float = 0.0


class RunBacktestInteractor(RunBacktestInputBoundary):
    def __init__(self, *, strategy: Any, indicators: Any, tick_model: Any) -> None:
        self._strategy = strategy
        self._indicators = indicators
        self._tick_model = tick_model

    @staticmethod
    def _close_open_trade(
        ot: _OpenTrade,
        *,
        exit_time: Any,
        exit_price: float,
        exit_reason: str,
        contract_size: float,
        leverage: float,
        account: Account,
        trades: list,
        deals: list,
        balance_curve: list,
    ) -> None:
        """保有玉 1 件を確定決済する（reverse / SL / TP で共通の決済処理）。

        手順は確定トレード列・margin・deal・balance_curve への反映を 1 箇所に集約する
        （reverse 決済と SL/TP 決済で重複していた処理の単一化）。振る舞いは不変。
        """
        trade = TradeRecord(
            side=ot.position.side,
            volume=ot.position.volume,
            entry_time=ot.entry_time,
            exit_time=exit_time,
            entry_price=ot.entry_price,
            exit_price=exit_price,
            contract_size=contract_size,
            swap=0.0,
            commission=0.0,
            exit_reason=exit_reason,
        )
        trades.append(trade)
        account.open_positions.remove(ot.position)
        account.margin -= ot.position.required_margin(leverage, contract_size)
        deal = _close_deal(trade)
        deals.append(deal)
        account.apply_deal(deal)
        balance_curve.append(account.balance)

    def execute(self, request: RunBacktestRequest) -> BacktestResult:
        config = request.config
        bars = list(request.bars)

        # OnInit 前処理
        self._strategy.on_init(config, self._indicators)

        trades: list[TradeRecord] = []
        deals: list = []
        balance_curve: list[float] = []
        equity_curve: list[float] = []
        open_trades: list[_OpenTrade] = []
        spec = request.symbol_spec
        contract_size = spec.contract_size
        account = Account(balance=request.initial_deposit, contract_size=contract_size)

        # tick ループ（PROCESS §2 A〜I を 1 bar = 1 OnTick として処理）
        for bar_index, bar in enumerate(bars):
            # C 指標値の取得（前計算系列から現足インデックスを引く）
            self._indicators.update(bar_index)
            # D 保有状態 / E シグナル評価（EA ロジック）
            orders = self._strategy.on_new_bar(bar_index, self._indicators, None) or []
            # F 発注（成行約定。スプレッドは bid/ask に内包。最小骨格は spread=0）
            bid = bar.close
            ask = bar.close
            for order in orders:
                # 反対サイドの保有玉があれば reverse 決済する（PROCESS §6）
                reverse_kept: list[_OpenTrade] = []
                for ot in open_trades:
                    if ot.position.side != order.side:
                        close_price = bid if ot.position.side == "buy" else ask
                        self._close_open_trade(
                            ot,
                            exit_time=bar.time,
                            exit_price=close_price,
                            exit_reason="reverse",
                            contract_size=contract_size,
                            leverage=spec.leverage,
                            account=account,
                            trades=trades,
                            deals=deals,
                            balance_curve=balance_curve,
                        )
                    else:
                        reverse_kept.append(ot)
                open_trades = reverse_kept

                position = fill_market_order(order, bid=bid, ask=ask)
                account.open_positions.append(position)
                account.margin += position.required_margin(spec.leverage, contract_size)
                open_trades.append(
                    _OpenTrade(
                        position=position,
                        sl=order.sl,
                        tp=order.tp,
                        entry_time=bar.time,
                        entry_price=position.entry_price,
                        opened_bar_index=bar_index,
                    )
                )
            # H 保有ポジの SL/TP ヒット判定（fill_delay=次tick: 発注足は監視しない）
            still_open: list[_OpenTrade] = []
            for ot in open_trades:
                if ot.opened_bar_index == bar_index:
                    still_open.append(ot)  # 発注足は次tick以降まで監視しない
                    continue
                reason = check_sltp_hit(
                    ot.position,
                    high=bar.high,
                    low=bar.low,
                    sl=ot.sl,
                    tp=ot.tp,
                    sltp_tie=config.sltp_tie,
                )
                if reason is None:
                    still_open.append(ot)
                    continue
                exit_price = ot.sl if reason == "sl" else ot.tp
                self._close_open_trade(
                    ot,
                    exit_time=bar.time,
                    exit_price=exit_price,
                    exit_reason=reason,
                    contract_size=contract_size,
                    leverage=spec.leverage,
                    account=account,
                    trades=trades,
                    deals=deals,
                    balance_curve=balance_curve,
                )
            open_trades = still_open

            # I エクイティ/残高の更新（含み損益反映）→ margin_level < stop_out で Fail-Stop
            account.update_floating_pnl(bar)
            equity_curve.append(account.equity)
            if account.margin_level() < request.stop_out_level:
                raise MarginCallError(
                    "margin_level が stop_out_level を下回りました",
                    context={
                        "margin_level": account.margin_level(),
                        "stop_out_level": request.stop_out_level,
                    },
                    bar_index=bar_index,
                )

        # OnDeinit 集計
        stats = compute_stats(
            trades=trades,
            balance_curve=balance_curve,
            equity_curve=equity_curve,
            initial_deposit=request.initial_deposit,
        )
        return BacktestResult(
            trades=trades,
            deals=deals,
            equity_curve=equity_curve,
            balance_curve=balance_curve,
            stats=stats,
        )
