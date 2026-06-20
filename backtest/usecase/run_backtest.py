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
from backtest.usecase._execution import (
    check_sltp_hit,
    check_sltp_hit_at_tick,
    close_price_for,
    derive_quotes,
    fill_market_order,
)
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
        # trade と同一の通貨丸めを deal.profit にも適用（balance と pnl を一致させる）。
        profit_round_digits=trade.profit_round_digits,
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
    # warmup/trading_start（config-gated・既定 None=全バー取引＝後方互換）。
    # 指定時は bar.time < trading_start のバーを「指標 update のみ実施し、トレード/
    # equity_curve/stats から除外する」ウォームアップ区間として扱う（指標 seed の収束のみ
    # を目的とし、約定・損益・equity 記録を行わない）。時刻型は bar.time と比較可能な型
    # （numpy.datetime64 / epoch int）を想定する。
    trading_start: Any = None


class RunBacktestInteractor(RunBacktestInputBoundary):
    def __init__(
        self,
        *,
        strategy: Any,
        indicators: Any,
        tick_model: Any,
        session_calendar: Any = None,
    ) -> None:
        self._strategy = strategy
        self._indicators = indicators
        self._tick_model = tick_model
        # 市場開閉カレンダー（DI・既定 None=常時開場＝既定経路 byte-identical）。
        self._session_calendar = session_calendar
        # 約定損益の口座通貨丸め桁（execute/_execute_every_tick が config から設定）。
        # __init__ で明示初期化し「execute 経由で必ず設定済み」の前提を明確化する。
        self._profit_round_digits: "int | None" = None

    def _closed_bars(self, bars: list) -> "set[int]":
        """新規成行を約定しないバー index 集合（カレンダー未注入なら空集合）。"""
        if self._session_calendar is None:
            return set()
        return self._session_calendar.closed_bar_indices(bars)

    def _close_open_trade(
        self,
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
        （reverse 決済と SL/TP 決済で重複していた処理の単一化）。TradeRecord は本メソッド
        が唯一の生成点であり、約定損益の通貨丸め桁（self._profit_round_digits・既定 None）
        を確定トレードへ付与する。振る舞いは丸め桁未設定時は不変（後方互換）。
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
            profit_round_digits=self._profit_round_digits,
        )
        trades.append(trade)
        account.open_positions.remove(ot.position)
        account.margin -= ot.position.required_margin(leverage, contract_size)
        deal = _close_deal(trade)
        deals.append(deal)
        account.apply_deal(deal)
        balance_curve.append(account.balance)

    def execute(self, request: RunBacktestRequest) -> BacktestResult:
        # every-tick 経路への分岐（every-tick #5）。config.tick_model == "real_ticks"
        # のときのみ実ティック内側ループ経路へ委譲する。それ以外は冒頭で early-return
        # せず以降の現行 bar ループへ直行し、既定（bar-mode）経路を 1 行も変えない。
        if getattr(request.config, "tick_model", None) == "real_ticks":
            return self._execute_every_tick(request)

        config = request.config
        bars = list(request.bars)
        # 約定損益の口座通貨丸め桁（既定 None＝丸めず＝byte-identical）。確定トレード生成
        # （_close_open_trade）が本値を TradeRecord に付与し pnl/deal/balance を一致させる。
        self._profit_round_digits = getattr(config, "profit_round_digits", None)
        # 市場閉鎖バー（新規成行を約定しない）。既定 None→空集合で既定経路は不変。
        closed_bars = self._closed_bars(bars)

        # OnInit 前処理
        self._strategy.on_init(config, self._indicators)

        trades: list[TradeRecord] = []
        deals: list = []
        balance_curve: list[float] = []
        equity_curve: list[float] = []
        open_trades: list[_OpenTrade] = []
        spec = request.symbol_spec
        contract_size = spec.contract_size
        # 層2: 含み損益の評価基準を config から引く（既定 "close"＝従来 close 固定で不変）。
        # "bid_ask" 時は売り保有を Ask=close+spread×point で評価するため point_size を渡す。
        floating_pnl_basis = getattr(config, "floating_pnl_basis", "close")
        account = Account(
            balance=request.initial_deposit,
            contract_size=contract_size,
            floating_pnl_basis=floating_pnl_basis,
            point_size=spec.point_size,
        )
        # close_and_halt で stop_out 後に新規発注を抑止するフラグ（cycle4 バグ②）。
        halted = False

        # warmup/trading_start: 指定時のみウォームアップ区間を有効化（既定 None=全バー取引）。
        trading_start = request.trading_start
        # 層1: prime_first_trading_bar=True かつ trading_start 指定時、取引区間の最初の 1 バー
        # （bar.time >= trading_start となる最初のバー）を warmup 同様にプライム扱いする。
        # primed_done で 1 回だけ消費する（既定 False=無効＝従来不変）。
        prime_first = getattr(config, "prime_first_trading_bar", False)
        primed_done = False

        # tick ループ（PROCESS §2 A〜I を 1 bar = 1 OnTick として処理）
        for bar_index, bar in enumerate(bars):
            # C 指標値の取得（前計算系列から現足インデックスを引く）
            self._indicators.update(bar_index)
            # warmup 区間（bar.time < trading_start）は指標 seed 収束のみを行い、トレード
            # 評価・約定・SL/TP 監視・equity 記録をすべてスキップする（config-gated）。
            if trading_start is not None and bar.time < trading_start:
                continue
            # 層1: 取引区間の最初の 1 バーをプライム（アタッチ）として warmup 同様にスキップ。
            #   trading_start 指定 + prime_first 有効時のみ。1 回消費したら以降は通常取引。
            #   bar.time >= trading_start を明示検査（warmup continue への暗黙依存を排除）。
            if (
                prime_first
                and trading_start is not None
                and bar.time >= trading_start
                and not primed_done
            ):
                primed_done = True
                continue
            # D 保有状態 / E シグナル評価（EA ロジック）
            #   halt 後はシグナルを評価しても発注しない（玉を増やさない）。
            orders = (
                []
                if halted
                else (self._strategy.on_new_bar(bar_index, self._indicators, account) or [])
            )
            # 市場閉鎖バーは新規成行を約定しない（ドテン反転の reverse 決済も含め全約定を
            #   スキップ）。on_new_bar は評価済＝保有不変のため、戦略（保有側基準の
            #   level-trigger）が次の開場バーで自動再発注し、実 MT5 の fail→retry→開場約定を
            #   再現する。SL/TP(H)・equity/stop-out(I) は閉鎖バーでも従来どおり評価する。
            if bar_index in closed_bars:
                orders = []
            # F 発注（成行約定）。約定価格基準（config）→当該足の建値を一元化した
            #   derive_quotes（_execution）へ委譲する。決済価格は close_price_for で
            #   約定価格ルール（long 決済=bid / short 決済=ask）を一意に決める。
            bid, ask, fill_spread, fill_point = derive_quotes(
                bar,
                entry_price_basis=config.entry_price_basis,
                point_size=spec.point_size,
            )
            for order in orders:
                # 反対サイドの保有玉があれば reverse 決済する（PROCESS §6）
                reverse_kept: list[_OpenTrade] = []
                for ot in open_trades:
                    if ot.position.side != order.side:
                        close_price = close_price_for(ot.position.side, bid=bid, ask=ask)
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

                position = fill_market_order(
                    order, bid=bid, ask=ask, spread=fill_spread, point_size=fill_point
                )
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

            # I エクイティ/残高の更新（含み損益反映）→ margin_level < stop_out で停止処理
            account.update_floating_pnl(bar)
            equity_curve.append(account.equity)
            if account.margin_level() < request.stop_out_level:
                # 既定 "fail_stop": 従来どおり MarginCallError を送出し部分結果を破棄する。
                if config.stop_out_action != "close_and_halt":
                    raise MarginCallError(
                        "margin_level が stop_out_level を下回りました",
                        context={
                            "margin_level": account.margin_level(),
                            "stop_out_level": request.stop_out_level,
                        },
                        bar_index=bar_index,
                    )
                # "close_and_halt": 全保有玉を強制決済し、以降の新規発注を抑止して最終統計
                # まで完走する（cycle4 バグ②）。強制決済価格は「margin 割れを判定した時点
                # の現値」＝account.mark_price（update_floating_pnl と同一価格・bar.close
                # 基準）を用いる。成行建値が始値基準（current_open）でも、過ぎ去った始値で
                # なく割れ時点の close 現値で決済する（実 MT5 整合・ISSUE-019）。
                for ot in open_trades:
                    close_price = account.mark_price(bar, ot.position.side)
                    self._close_open_trade(
                        ot,
                        exit_time=bar.time,
                        exit_price=close_price,
                        exit_reason="stop_out",
                        contract_size=contract_size,
                        leverage=spec.leverage,
                        account=account,
                        trades=trades,
                        deals=deals,
                        balance_curve=balance_curve,
                    )
                open_trades = []
                halted = True

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

    def _execute_every_tick(self, request: RunBacktestRequest) -> BacktestResult:
        """every-tick 経路（PROCESS A〜I・config.tick_model=="real_ticks" 専用）。

        バー外側ループ + 実ティック内側ループ。確定足指標 update（C）と新規バー
        シグナル評価（D/E on_new_bar）は「足境界」でのみ行い、約定（F）・SL/TP 判定
        （H）・含み損評価/equity 記録/stop-out（I）は「ティック」で行う。bar-mode 経路
        （execute 本体）は不変で、本メソッドは並存する新経路。

        判断点（PROCESS §2/§5/§7 推奨案）:
            - ティック 0 件バー: 保有玉があれば最後の既知ティック価格（無ければ
              bar.close）で 1 点だけ floating/equity を記録する。当該バーで on_new_bar が
              返した orders は「約定タイミング（ティック）が存在しない」ため約定せず、
              次バーへ持ち越さない（実ティック不在の足では建玉しない＝実 MT5 整合）。
              保有玉も無いティック 0 件バーは equity_curve に点を追加しない（評価対象なし）。
            - bar-mode との非一致: equity 系 stats（equity_curve 長・DD）は「評価点が
              ティック数に依存する」ため bar-mode（1 バー 1 点）と一致しない。確定トレード
              （約定・決済価格）は縮退条件（1 バー 1 ティック=close・spread0）で一致する。
            - 約定価格の基準（非対称・意図的）: 成行約定（新規建て・reverse 決済）は
              「足境界のバー open クォート」（derive_quotes・bar-mode と同一）で約定する。
              一方 SL/TP・stop-out の決済は「到達/評価ティック価格」を用いる（ティック駆動
              の固有挙動）。基準が約定種別で異なる点に注意（実 MT5 every-tick 整合）。
            - fill_delay=next_tick: 建てたバー（opened_bar_index==bar_index）のティック
              では SL/TP 監視しない。次バー以降のティックで監視する。
            - SL/TP 同時到達: check_sltp_hit_at_tick が単一価格 p で high=low=p として
              config.sltp_tie の同点解消（既定 SL 優先）を継承する。
        """
        config = request.config
        bars = list(request.bars)
        # 約定損益の口座通貨丸め桁（既定 None＝丸めず＝byte-identical）。
        self._profit_round_digits = getattr(config, "profit_round_digits", None)
        # 市場閉鎖バー（新規成行を約定しない）。既定 None→空集合で挙動不変。
        closed_bars = self._closed_bars(bars)

        self._strategy.on_init(config, self._indicators)

        trades: list[TradeRecord] = []
        deals: list = []
        balance_curve: list[float] = []
        equity_curve: list[float] = []
        open_trades: list[_OpenTrade] = []
        spec = request.symbol_spec
        contract_size = spec.contract_size
        floating_pnl_basis = getattr(config, "floating_pnl_basis", "close")
        account = Account(
            balance=request.initial_deposit,
            contract_size=contract_size,
            floating_pnl_basis=floating_pnl_basis,
            point_size=spec.point_size,
        )
        halted = False

        trading_start = request.trading_start
        prime_first = getattr(config, "prime_first_trading_bar", False)
        primed_done = False

        prev_close: float | None = None
        # ティック 0 件バーで保有玉を評価するための直近既知 bid/ask（carry-forward）。
        last_bid: float | None = None
        last_ask: float | None = None

        for bar_index, bar in enumerate(bars):
            # C 確定足指標（足単位・bar-mode と同一）
            self._indicators.update(bar_index)
            # warmup 区間（bar.time < trading_start）は指標 seed 収束のみ。
            if trading_start is not None and bar.time < trading_start:
                prev_close = bar.close
                continue
            # 取引区間の最初の 1 バーをプライム（warmup 同様にスキップ・1 回消費）。
            if (
                prime_first
                and trading_start is not None
                and bar.time >= trading_start
                and not primed_done
            ):
                primed_done = True
                prev_close = bar.close
                continue

            # D/E ★足境界のみ: 新規バーシグナル評価（ティックで呼ばない）。
            #   halt 後は発注しない（玉を増やさない）。
            pending_orders = (
                []
                if halted
                else (self._strategy.on_new_bar(bar_index, self._indicators, account) or [])
            )

            # 当該足のティックを materialize（空判定のため一覧化）。実 MT5 every-tick は
            # 新規バーを「最初のティック」で検知するため、ティック 0 件足では新規バーを
            # 検知せず発注しない（次足へ持ち越さない＝実 MT5 整合）。
            bar_ticks = list(self._tick_model.ticks_of(bar, prev_close))

            # F ★足境界・バー open クォートで成行約定（bar-mode と同一: derive_quotes）。
            #   実 MT5 は新規バーの成行を「バー open のクォート」（買い=open+spread×point、
            #   売り=open）で約定し、ティックは含み損/SL-TP/stop-out の評価にのみ用いる
            #   （bar-mode 突合 2025-01 と同一の建値ルール）。ティック 0 件足では発注しない。
            #   市場閉鎖バー（closed_bars）も新規成行を約定しない（ドテン反転の reverse
            #   決済も含め全約定を抑止。保有不変＝戦略が次の開場バーで自動再発注）。
            if bar_ticks and pending_orders and bar_index not in closed_bars:
                bid0, ask0, fill_spread, fill_point = derive_quotes(
                    bar,
                    entry_price_basis=config.entry_price_basis,
                    point_size=spec.point_size,
                )
                for order in pending_orders:
                    # 反対サイドの保有玉があれば bar open クォートで reverse 決済。
                    reverse_kept: list[_OpenTrade] = []
                    for ot in open_trades:
                        if ot.position.side != order.side:
                            close_price = close_price_for(
                                ot.position.side, bid=bid0, ask=ask0
                            )
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

                    position = fill_market_order(
                        order, bid=bid0, ask=ask0, spread=fill_spread, point_size=fill_point
                    )
                    account.open_positions.append(position)
                    account.margin += position.required_margin(
                        spec.leverage, contract_size
                    )
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

            saw_tick = False
            for tick in bar_ticks:
                price, bid, ask, _tick_time = tick
                saw_tick = True
                last_bid, last_ask = bid, ask

                # H 保有玉 SL/TP を到達ティック価格で判定（fill_delay=次tick: 発注足は監視外）。
                still_open: list[_OpenTrade] = []
                for ot in open_trades:
                    if ot.opened_bar_index == bar_index:
                        still_open.append(ot)  # 建てた足のティックでは監視しない
                        continue
                    reason = check_sltp_hit_at_tick(
                        ot.position,
                        price=price,
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

                # I ティック評価価格で含み損更新 → equity 記録 → stop-out 判定。
                account.update_floating_pnl_at(bid=bid, ask=ask)
                equity_curve.append(account.equity)
                if account.margin_level() < request.stop_out_level:
                    if config.stop_out_action != "close_and_halt":
                        raise MarginCallError(
                            "margin_level が stop_out_level を下回りました",
                            context={
                                "margin_level": account.margin_level(),
                                "stop_out_level": request.stop_out_level,
                            },
                            bar_index=bar_index,
                        )
                    for ot in open_trades:
                        close_price = close_price_for(
                            ot.position.side, bid=bid, ask=ask
                        )
                        self._close_open_trade(
                            ot,
                            exit_time=bar.time,
                            exit_price=close_price,
                            exit_reason="stop_out",
                            contract_size=contract_size,
                            leverage=spec.leverage,
                            account=account,
                            trades=trades,
                            deals=deals,
                            balance_curve=balance_curve,
                        )
                    open_trades = []
                    halted = True

            # ティック 0 件バー: 保有玉があれば直近既知 bid/ask（無ければ close）で 1 点記録。
            if not saw_tick and open_trades:
                eval_bid = last_bid if last_bid is not None else bar.close
                eval_ask = last_ask if last_ask is not None else bar.close
                account.update_floating_pnl_at(bid=eval_bid, ask=eval_ask)
                equity_curve.append(account.equity)

            prev_close = bar.close

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
