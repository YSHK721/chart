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

from simulator.domain.account import Account
from simulator.domain.deal import Deal
from simulator.domain.exceptions import MarginCallError
from simulator.domain.position import Position
from simulator.domain.trade_record import TradeRecord
from simulator.usecase._execution import (
    admit_orders,
    check_sltp_hit,
    check_sltp_hit_at_tick,
    close_price_for,
    derive_quotes,
    fill_market_order,
    resolve_eval_quote,
)
from simulator.usecase.compute_stats import compute_stats
from simulator.usecase.models import AccountSpec, BacktestResult
from simulator.usecase.pending_lifecycle import PendingLifecycleEngine
from simulator.usecase.ports import RunBacktestInputBoundary
from simulator.usecase.run_features import RunFeatures
from simulator.usecase.session_gate import SessionGate
from simulator.usecase.stop_out_policy import StopOutContext, resolve_stop_out_policy


# 部分決済（FR-08）の確定トレードに付す exit_reason。部分決済は実現 Deal であり、full-TP-hit
# の "tp" と区別する必要がある（統計・マーカーで混同すると実トレードと乖離する）。依頼者裁定
# （2026-08-13）により domain/trade_record.py の許可集合へ "partial" を追加し、正しく分類する
# （抜本的解決）。既定 pm=None の golden 経路では本理由は一切生成されない（部分決済は opt-in）
# ため byte 等価は不変。
_PARTIAL_CLOSE_EXIT_REASON = "partial"

# 証拠金割れで run を捨てるときの文言。評価点の名前（_STOP_OUT_AT_*）を末尾に足す。
_STOP_OUT_BREACH_MESSAGE = "margin_level が stop_out_level を下回りました"
# 評価点の名前。バー open 評価だけは「どの pseudo-tick で割れたか」が診断に要るため
# 文言に残す（移設前と byte 一致）。他の 2 点は評価点名を付けない。
_STOP_OUT_AT_BAR_OPEN = "（bar open 評価）"
_STOP_OUT_AT_EVALUATION = ""


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
    # 建てた足のティックで SL/TP 監視を抑止するか（成行=True で従来どおり「発注足は次tick
    # 以降まで監視しない」。ペンディング約定=False で「約定ティックより後は同一足内でも監視」）。
    # ペンディングは足途中の特定ティックで約定し、その後の同足ティックで SL/TP が決済され
    # 得る（実 MT5: 1 本の M1 足内で trigger→fill→SL/TP が連鎖。2603-01 journal で実証）。
    skip_entry_bar: bool = True
    # ペンディング約定時の建てたティックの序数（0=open）。同一足では「この序数より後」の
    # ティックでのみ SL/TP 監視する（約定ティック自身では判定しない＝実 MT5 server 整合）。
    opened_tick_ordinal: int = -1


@dataclass
class _RunState:
    """1 run の開始状態（両実行経路で完全一致していた準備段の産物）。

    保持するのは「run の間ずっと同じであり続けるもの」（銘柄仕様・口座・記録先・
    セッション判定・取引区間の設定）と、「run の進行で書き換わるもの」の**初期値**
    （保有玉・halt・プライム消費済フラグ）である。

    なぜ 1 つの値にまとめるか: 準備段は 2 つのエンジンに字句レベルで書き写されており、
    片方だけに項目が足される形の食い違いを検定で捉えられなかった（両経路が同じ
    fixture を通らない限り数値の指紋に現れない）。組み立ての定義点を 1 つにすれば、
    その食い違い自体が起こり得なくなる。

    `close_trade` は決済呼び出しの**不変の文脈**（口座・記録先・銘柄仕様・レバレッジ）を
    束ねた呼び口である（ISSUE-308）。各決済地点が同じ 4 つを書き写す必要をなくす。
    """

    bars: list
    features: RunFeatures
    spec: Any
    contract_size: float
    floating_pnl_basis: str
    account: Account
    session_gate: SessionGate
    stop_out_policy: Any
    close_trade: Any
    trades: list
    deals: list
    balance_curve: list
    equity_curve: list
    open_trades: list
    halted: bool
    trading_start: Any
    prime_first: bool
    primed_done: bool


@dataclass
class RunBacktestRequest:
    """UC-001 Input Model（CLEAN_ARCH §3: {config, data}）。

    data は domain の Bar 列（イテラブル）。DataFrame→Bar 変換は将来 adapter 責務。
    """

    config: Any
    bars: Any
    symbol_spec: Any
    # 契約は 2 軸（ISSUE-445 段階 3-D3・設計書 §3.4）。`symbol_spec`＝銘柄の契約、
    # `account`＝口座の契約。段階 3-D2 では口座属性（`initial_deposit` / `leverage` /
    # `stop_out_level`）を本 DTO が 3 つフラットに持っていたが、供給元スナップショットの
    # `account` セクションは既に 5 キーを持ち、実口座からは `margin_mode` /
    # `margin_so_call` / `margin_so_so` も実測記録されている。口座属性が増えるたびに
    # 本 DTO を改変するのは OCP 違反であるため、`AccountSpec` に閉じる。
    # **既定値は持たない**（`AccountSpec` 側も全フィールド既定なし）。
    account: AccountSpec
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
        position_manager: Any = None,
    ) -> None:
        self._strategy = strategy
        self._indicators = indicators
        self._tick_model = tick_model
        # 市場開閉カレンダー（DI・既定 None=常時開場＝既定経路 byte-identical）。
        self._session_calendar = session_calendar
        # 建玉変更（トレーリング FR-07・部分決済 FR-08）の適用器（DI・既定 None＝無変更
        # ＝既定経路 byte-identical・Phase 7）。None のときは呼出点を素通りする（`if pm is
        # not None` ゲート）。注入時のみ B2（bar）/B4（tick）で保有玉を評価する。
        self._position_manager = position_manager
        # 約定損益の口座通貨丸め桁（execute/_execute_every_tick が config から設定）。
        # __init__ で明示初期化し「execute 経由で必ず設定済み」の前提を明確化する。
        self._profit_round_digits: "int | None" = None

    def _session_gate(self, bars: list) -> SessionGate:
        """closed_bars セッション判定を集約した SessionGate を構築する（ISSUE-094）。

        従来の `_closed_bars`（新規成行を約定しないバー index 集合の導出）を
        SessionGate.from_calendar へ委譲する。カレンダー未注入なら空集合＝常時開場。
        """
        return SessionGate.from_calendar(self._session_calendar, bars)

    def _begin_run(
        self, request: RunBacktestRequest, features: "RunFeatures | None" = None
    ) -> _RunState:
        """run の開始状態を組み立てる（両実行経路で完全一致していた準備段の単一化）。

        副作用の順序は移設前と同一である（この順序自体が仕様）:
            1. 約定損益の丸め桁を確定する（以降の確定トレード生成が参照する）
            2. セッション判定（閉鎖バー集合）を導出する
            3. 戦略を初期化する（OnInit）

        2 が 3 より先である理由: 戦略の OnInit は config を受けて自分の状態を組む。
        その前に「どのバーが閉鎖か」を確定しておかないと、戦略が走り出した後で
        エンジン側の世界が組み上がることになり、成立順が run ごとに揺れうる。

        口座（`Account`）の構築は OnInit の後である（移設前と同一）。
        """
        config = request.config
        # run のスイッチは 1 度だけ読む（既定は BacktestConfig の宣言が単一ソース）。
        #   呼出側が既に読んでいれば読み直さない（読み取りは run につき 1 回）。
        features = features if features is not None else RunFeatures.of(config)
        bars = list(request.bars)
        # 約定損益の口座通貨丸め桁（既定 None＝丸めず＝byte-identical）。確定トレード生成
        # （_close_open_trade）が本値を TradeRecord に付与し pnl/deal/balance を一致させる。
        self._profit_round_digits = features.profit_round_digits
        # 市場閉鎖バー（新規成行を約定しない）。既定 None→空集合で既定経路は不変。
        session_gate = self._session_gate(bars)

        # OnInit 前処理
        self._strategy.on_init(config, self._indicators)

        trades: list[TradeRecord] = []
        deals: list = []
        balance_curve: list[float] = []
        equity_curve: list[float] = []
        spec = request.symbol_spec
        contract_size = spec.contract_size
        # 層2: 含み損益の評価基準を config から引く（既定 "close"＝従来 close 固定で不変）。
        # "bid_ask" 時は売り保有を Ask=close+spread×point で評価するため point_size を渡す。
        floating_pnl_basis = features.floating_pnl_basis
        account = Account(
            balance=request.account.initial_deposit,
            contract_size=contract_size,
            floating_pnl_basis=floating_pnl_basis,
            point_size=spec.point_size,
        )

        # ISSUE-308: 決済呼び出しの**不変の文脈**（口座・記録先・銘柄仕様）をここで 1 度だけ束ねる。
        #   これらは 1 回の実行中に変わらないため、各決済地点で書き写す必要がない。
        def close_trade(ot, *, exit_time, exit_price, exit_reason, close_volume=None) -> None:
            self._close_open_trade(
                ot,
                exit_time=exit_time,
                exit_price=exit_price,
                exit_reason=exit_reason,
                contract_size=contract_size,
                leverage=request.account.leverage,
                account=account,
                trades=trades,
                deals=deals,
                balance_curve=balance_curve,
                close_volume=close_volume,
            )

        return _RunState(
            bars=bars,
            features=features,
            spec=spec,
            contract_size=contract_size,
            floating_pnl_basis=floating_pnl_basis,
            account=account,
            session_gate=session_gate,
            # 証拠金割れの方針は run につき 1 度だけ引く（評価点ごとに引き直さない）。
            stop_out_policy=resolve_stop_out_policy(features.stop_out_action),
            close_trade=close_trade,
            trades=trades,
            deals=deals,
            balance_curve=balance_curve,
            equity_curve=equity_curve,
            # 保有玉（走査順＝反映順が byte 依存）。
            open_trades=[],
            # close_and_halt で stop_out 後に新規発注を抑止するフラグ（cycle4 バグ②）。
            halted=False,
            # warmup/trading_start: 指定時のみウォームアップ区間を有効化（既定 None=全バー取引）。
            trading_start=request.trading_start,
            # 層1: prime_first_trading_bar=True かつ trading_start 指定時、取引区間の最初の
            # 1 バー（bar.time >= trading_start となる最初のバー）を warmup 同様にプライム扱い
            # する。primed_done で 1 回だけ消費する（既定 False=無効＝従来不変）。
            prime_first=features.prime_first_trading_bar,
            primed_done=False,
        )

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
        close_volume: "float | None" = None,
    ) -> None:
        """保有玉 1 件を確定決済する（reverse / SL / TP / 部分決済で共通の決済処理）。

        手順は確定トレード列・margin・deal・balance_curve への反映を 1 箇所に集約する
        （reverse 決済と SL/TP 決済で重複していた処理の単一化）。TradeRecord は本メソッド
        が唯一の生成点であり、約定損益の通貨丸め桁（self._profit_round_digits・既定 None）
        を確定トレードへ付与する。振る舞いは丸め桁未設定時は不変（後方互換）。

        close_volume（Phase 7 FR-08・部分決済の単一ソース化）:
            None（既定）= 全量決済。``v = ot.position.volume`` で従来と byte-identical。
            指定時 = 部分決済。``v = close_volume`` を決済し、margin/swap/commission を
            v/total 比で按分解放する（swap=commission=0 のため 0×比=0＝全量経路 byte 不変）。
            残玉（v < total）は frozen Position を ``Position(side, total−v, entry)`` で縮小
            再構築し、account.open_positions を**同 index 置換**（走査順=反映順の byte 依存を
            保持）、ot.position も残玉へ差し替える（ot.sl/ot.tp は不変で建玉時 SL/TP を維持）。
            v == total は現状どおり remove（byte 等価）。約定数学は _execution・Deal.from_close
            を共有し、写経しない。
        """
        total = ot.position.volume
        v = close_volume if close_volume is not None else total
        trade = TradeRecord(
            side=ot.position.side,
            volume=v,
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
        # margin を v/total 比で按分解放（v==total で比=1.0＝全量経路と byte 一致）。
        account.margin -= ot.position.required_margin(leverage, contract_size) * (v / total)
        if close_volume is not None and v < total:
            # 部分決済: 残玉を縮小再構築し open_positions を同 index 置換（走査順を保持）。
            residual = Position(
                side=ot.position.side, volume=total - v, entry_price=ot.position.entry_price
            )
            idx = account.open_positions.index(ot.position)
            account.open_positions[idx] = residual
            ot.position = residual  # ot も残玉へ（同一 ot が生存継続・sl/tp は不変）
        else:
            account.open_positions.remove(ot.position)
        deal = _close_deal(trade)
        deals.append(deal)
        account.apply_deal(deal)
        balance_curve.append(account.balance)

    def _apply_directive(
        self,
        directive: Any,
        ot: _OpenTrade,
        *,
        close_trade: Any,
        exit_time: Any,
        exit_price: float,
    ) -> None:
        """PositionDirective を保有玉へ適用する（Phase 7・忠実適用順序）。

        hit 判定（H）の後に呼ばれ、部分決済（実現 Deal・証拠金を按分解放）→ SL/TP 更新
        （ot.sl/ot.tp 書換・次評価点から効く）の順で反映する。SL/TP 更新のみのトレーリングは
        TradeRecord を生成しない（含み玉の SL を締めるだけ）。directive が None/無変更なら
        何もしない（既定経路 byte 不変）。部分決済は :meth:`_close_open_trade` を close_volume
        付きで呼び、全量経路と同一の約定数学（_execution・Deal.from_close）を共有する。

        部分決済のフィル価格は directive.close_price を用いる（bar 粒度＝トリガー水準／tick
        粒度＝現在価格）。到達検出（極値 touch）とフィル価格を分離する（依頼者裁定 2026-08-13：
        bar は部分 TP としてトリガー水準で約定・極値でフィルしない）。close_price 未指定
        （None）の場合のみ呼出側の exit_price へフォールバックする（防御的）。
        """
        if directive is None or directive.is_noop():
            return
        if directive.close_volume is not None:
            fill_price = (
                directive.close_price if directive.close_price is not None else exit_price
            )
            close_trade(
                ot,
                exit_time=exit_time,
                exit_price=fill_price,
                exit_reason=_PARTIAL_CLOSE_EXIT_REASON,
                close_volume=directive.close_volume,
            )
        if directive.new_sl is not None:
            ot.sl = directive.new_sl
        if directive.new_tp is not None:
            ot.tp = directive.new_tp

    def execute(self, request: RunBacktestRequest) -> BacktestResult:
        # every-tick 経路への分岐（every-tick #5）。config.tick_model == "real_ticks"
        # のときのみ実ティック内側ループ経路へ委譲する。それ以外は冒頭で early-return
        # せず以降の現行 bar ループへ直行し、既定（bar-mode）経路を 1 行も変えない。
        features = RunFeatures.of(request.config)
        if features.tick_model == "real_ticks" or features.pending_lifecycle:
            return self._execute_every_tick(request, features)

        state = self._begin_run(request, features)
        bars = state.bars
        spec = state.spec
        contract_size = state.contract_size
        floating_pnl_basis = state.floating_pnl_basis
        account = state.account
        session_gate = state.session_gate
        close_trade = state.close_trade
        trades = state.trades
        deals = state.deals
        balance_curve = state.balance_curve
        equity_curve = state.equity_curve
        open_trades = state.open_trades
        halted = state.halted
        trading_start = state.trading_start
        prime_first = state.prime_first
        primed_done = state.primed_done
        # stop-out をバー open でも先行評価するか（config gated・既定 False=従来 close のみ）。
        stop_out_at_open = features.stop_out_at_open

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
            # I' ★バー open での stop-out 先行判定（config gated）。実 MT5 1分足OHLC は
            #   O→H→L→C の最初の pseudo-tick（open）で margin を評価するため、週末ギャップ等で
            #   open が割れた保有玉は「バー open クォート」で強制決済される（買い=Bid=open /
            #   売り=Ask=open+spread×point）。後段の close 基準判定（I）は残すため、open が割れず
            #   bar 内で割れる場合は従来どおり close で決済する。既定 False で本ブロックは不活性＝
            #   byte-identical（ISSUE-022）。open 評価は floating_pnl_basis を参照せず実 bid/ask
            #   固定（買い=Bid=open / 売り=Ask=open+spread＝MT5 open pseudo-tick 整合）。
            if stop_out_at_open and open_trades and not halted:
                o_bid, o_ask, _, _ = derive_quotes(
                    bar, entry_price_basis="current_open", point_size=spec.point_size
                )
                account.update_floating_pnl_at(bid=o_bid, ask=o_ask)
                if account.margin_level() < request.account.stop_out_level:
                    self._apply_stop_out(
                        policy=state.stop_out_policy,
                        open_trades=open_trades,
                        close_trade=close_trade,
                        account=account,
                        stop_out_level=request.account.stop_out_level,
                        bar=bar,
                        bar_index=bar_index,
                        bid=o_bid,
                        ask=o_ask,
                        where=_STOP_OUT_AT_BAR_OPEN,
                    )
                    open_trades = []
                    halted = True
                else:
                    # 非breach: open 基準で書き換えた floating を close 基準へ戻す
                    #   （後続 on_new_bar 等へ open 基準 floating を漏らさない。equity_curve は
                    #   後段 I で close 基準により記録する）。レビュー🟡対応。
                    #   評価価格は usecase 側で解決（🟡-10b: 執行クォート規約を domain から分離）。
                    nb_bid, nb_ask = resolve_eval_quote(
                        bar, basis=floating_pnl_basis, point_size=spec.point_size
                    )
                    account.update_floating_pnl_at(bid=nb_bid, ask=nb_ask)
            # D 保有状態 / E シグナル評価（EA ロジック）
            #   halt 後はシグナルを評価しても発注しない（玉を増やさない）。
            #   戦略の戻り値は admit_orders（受理の唯一の門・ISSUE-445 段階 3-C）を通す。
            orders = (
                []
                if halted
                else admit_orders(
                    self._strategy.on_new_bar(bar_index, self._indicators, account) or [],
                    spec,
                )
            )
            # 市場閉鎖バーは新規成行を約定しない（ドテン反転の reverse 決済も含め全約定を
            #   スキップ）。on_new_bar は評価済＝保有不変のため、戦略（保有側基準の
            #   level-trigger）が次の開場バーで自動再発注し、実 MT5 の fail→retry→開場約定を
            #   再現する。SL/TP(H)・equity/stop-out(I) は閉鎖バーでも従来どおり評価する。
            if session_gate.is_closed(bar_index):
                orders = []
            # F 発注（成行約定）。約定価格基準（config）→当該足の建値を一元化した
            #   derive_quotes（_execution）へ委譲する。決済価格は close_price_for で
            #   約定価格ルール（long 決済=bid / short 決済=ask）を一意に決める。
            bid, ask, fill_spread, fill_point = derive_quotes(
                bar,
                entry_price_basis=features.entry_price_basis,
                point_size=spec.point_size,
            )
            for order in orders:
                # 反対サイドの保有玉があれば reverse 決済する（PROCESS §6）
                reverse_kept: list[_OpenTrade] = []
                for ot in open_trades:
                    if ot.position.side != order.side:
                        close_price = close_price_for(ot.position.side, bid=bid, ask=ask)
                        close_trade(
                            ot,
                            exit_time=bar.time,
                            exit_price=close_price,
                            exit_reason="reverse",
                        )
                    else:
                        reverse_kept.append(ot)
                open_trades = reverse_kept

                position = fill_market_order(
                    order, bid=bid, ask=ask, spread=fill_spread, point_size=fill_point
                )
                account.open_positions.append(position)
                account.margin += position.required_margin(request.account.leverage, contract_size)
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
            #   閉鎖バー（市場閉鎖）では SL/TP（顧客注文）を処理しない＝トレードセッション・ゲート
            #   （every-tick 経路と一貫）。stop-out（リスク清算・後段 I）は閉鎖バーでも継続する。
            still_open: list[_OpenTrade] = []
            for ot in open_trades:
                if session_gate.is_closed(bar_index):
                    still_open.append(ot)  # 閉鎖バーは SL/TP 監視外（セッション外）
                    continue
                if ot.opened_bar_index == bar_index:
                    still_open.append(ot)  # 発注足は次tick以降まで監視しない
                    continue
                reason = check_sltp_hit(
                    ot.position,
                    high=bar.high,
                    low=bar.low,
                    sl=ot.sl,
                    tp=ot.tp,
                    sltp_tie=features.sltp_tie,
                )
                if reason is None:
                    still_open.append(ot)
                    continue
                exit_price = ot.sl if reason == "sl" else ot.tp
                close_trade(
                    ot,
                    exit_time=bar.time,
                    exit_price=exit_price,
                    exit_reason=reason,
                )
            open_trades = still_open

            # B2 建玉変更（トレーリング FR-07・部分決済 FR-08）を hit 判定（H）の後・I の前に
            #   適用する（Phase 7・忠実順序: サーバ hit → EA 動作 → 口座再評価）。既定 pm=None
            #   は素通り＝byte-identical。参照/決済価格はトレーリング方向の到達価格（buy=high /
            #   sell=low）で、check_sltp_hit が high/low で touch を見るのと対称にする（close だと
            #   SL/TP 判定と非対称になる）。閉鎖バーは EA 動作外として適用しない（H と一貫）。
            pm = self._position_manager
            if pm is not None and open_trades and not session_gate.is_closed(bar_index):
                for ot in list(open_trades):
                    ref = bar.high if ot.position.side == "buy" else bar.low
                    directive = pm.evaluate(
                        ot=ot, ref_price=ref, granularity="bar", account=account
                    )
                    self._apply_directive(
                        directive, ot, close_trade=close_trade,
                        exit_time=bar.time, exit_price=ref,
                    )

            # I エクイティ/残高の更新（含み損益反映）→ margin_level < stop_out で停止処理
            #   評価価格は usecase 側で解決（🟡-10b: 執行クォート規約を domain から分離）。
            eq_bid, eq_ask = resolve_eval_quote(
                bar, basis=floating_pnl_basis, point_size=spec.point_size
            )
            account.update_floating_pnl_at(bid=eq_bid, ask=eq_ask)
            equity_curve.append(account.equity)
            if account.margin_level() < request.account.stop_out_level:
                # 強制決済のクォートは、直上で含み損を評価したのと同じ (eq_bid, eq_ask)。
                #   移設前はここで resolve_eval_quote を同じ引数でもう一度呼んでいたが、
                #   同じ純関数を同じ入力で二度引いた値であり、常に等しい（捨てる計算）。
                self._apply_stop_out(
                    policy=state.stop_out_policy,
                    open_trades=open_trades,
                    close_trade=close_trade,
                    account=account,
                    stop_out_level=request.account.stop_out_level,
                    bar=bar,
                    bar_index=bar_index,
                    bid=eq_bid,
                    ask=eq_ask,
                    where=_STOP_OUT_AT_EVALUATION,
                )
                open_trades = []
                halted = True

        # OnDeinit 集計
        return self._finish_run(
            trades=trades,
            deals=deals,
            balance_curve=balance_curve,
            equity_curve=equity_curve,
            initial_deposit=request.account.initial_deposit,
        )

    def _apply_stop_out(
        self,
        *,
        policy: Any,
        open_trades: list,
        close_trade: Any,
        account: Account,
        stop_out_level: float,
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
        decision = policy.on_breach(
            StopOutContext(
                margin_level=account.margin_level(),
                stop_out_level=stop_out_level,
                bar_index=bar_index,
                open_trade_count=len(open_trades),
            )
        )
        if not decision.liquidate:
            raise MarginCallError(
                _STOP_OUT_BREACH_MESSAGE + where,
                context={
                    "margin_level": account.margin_level(),
                    "stop_out_level": stop_out_level,
                },
                bar_index=bar_index,
            )
        for ot in open_trades:
            close_price = close_price_for(ot.position.side, bid=bid, ask=ask)
            close_trade(
                ot,
                exit_time=bar.time,
                exit_price=close_price,
                exit_reason="stop_out",
            )

    def _finish_run(
        self,
        *,
        trades: list,
        deals: list,
        balance_curve: list,
        equity_curve: list,
        initial_deposit: float,
    ) -> BacktestResult:
        """OnDeinit 集計段（両実行経路で完全一致していた終了処理の単一化）。

        両経路の終了段（移設前 `execute` :515-527 / `_execute_every_tick` :1027-1039）は
        字句まで同一だった。同じ処理が 2 箇所に在ると「片方だけが更新される」形の欠陥
        （例: 統計へ渡す系列を 1 本足し忘れる）が起こり得るのに、両経路が同じ検定を
        通らない限り検出できない。集計の定義点を 1 つにして、その食い違い自体を
        構造的に不能にする。

        振る舞いは不変: `compute_stats` へ渡す 4 引数と `BacktestResult` の 5 フィールドは
        移設前と同一の値である（G0 の sha256 指紋が 1 bit の変化も赤にする）。
        """
        stats = compute_stats(
            trades=trades,
            balance_curve=balance_curve,
            equity_curve=equity_curve,
            initial_deposit=initial_deposit,
        )
        return BacktestResult(
            trades=trades,
            deals=deals,
            equity_curve=equity_curve,
            balance_curve=balance_curve,
            stats=stats,
        )

    def _execute_every_tick(
        self, request: RunBacktestRequest, features: "RunFeatures | None" = None
    ) -> BacktestResult:
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
        features = features if features is not None else RunFeatures.of(request.config)
        state = self._begin_run(request, features)
        bars = state.bars
        spec = state.spec
        contract_size = state.contract_size
        floating_pnl_basis = state.floating_pnl_basis
        account = state.account
        session_gate = state.session_gate
        close_trade = state.close_trade
        trades = state.trades
        deals = state.deals
        balance_curve = state.balance_curve
        equity_curve = state.equity_curve
        open_trades = state.open_trades
        halted = state.halted
        trading_start = state.trading_start
        prime_first = state.prime_first
        primed_done = state.primed_done
        # ペンディング（指値/逆指値）ライフサイクル経路か（既定 False＝real_ticks 等は不変）。
        pending_mode = features.pending_lifecycle
        # 同時設置ペンディングの OCO（既定 False＝兄弟は独立約定／単一ペンディングEAでは無影響）。
        # True で 1 本約定時に残る兄弟ペンディングを全取消する（StopEntryProbe の両建て用）。
        pending_oco = features.pending_oco
        # ペンディング持続＋足途中ティック再アーム（既定 False＝従来 cancel-and-replace）。
        # True で resting をバー境界でリセットせず約定まで保持し、フラット＆未装填のティックで
        # strategy.on_tick を呼び当該ティッククォートで即再装填する（StopEntryProbe 用）。
        pending_persistent = features.pending_persistent
        # hedging 口座の両建て証拠金相殺（既定 False＝従来の単純加算）。True で stop-out 判定の
        # 証拠金を「買い計・売り計の大きい側」とする（反対玉は相殺＝同量両建ては stop-out しない）。
        hedged_margin = features.hedged_margin
        # 建玉変更の適用器（Phase 7・既定 None＝素通り＝byte-identical）。B4 で参照する。
        pm = self._position_manager

        prev_close: float | None = None
        # 残存ペンディング（指値/逆指値・最大 1 件）。前足で設置され未約定のまま次足へ持ち越し、
        # 次足 open tick で 1 回トリガ評価される（実 MT5: サーバが OnTick 前に評価）。on_new_bar
        # で EA が削除し最新シグナルで再設置する。pending_mode 以外では常に空で挙動不変。
        resting_pending: list = []
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

            # 当該足のティックを materialize（open-tick SL/TP・空判定・トリガ評価に先行使用）。
            # 実 MT5 every-tick は新規バーを「最初のティック」で検知するため、ティック 0 件足
            # では新規バーを検知せず発注しない（次足へ持ち越さない＝実 MT5 整合）。
            bar_ticks = list(self._tick_model.ticks_of(bar, prev_close))

            # ★ペンディング経路: 実 MT5 はバー先頭ティック(open)でサーバが保有玉の SL/TP を
            #   処理してから EA の OnTick(on_new_bar) を呼ぶ。よって on_new_bar より先に、前足
            #   からの保有玉の SL/TP を open クォート（buy=Bid=open / sell=Ask=open+spread×point）
            #   で判定する。open で SL/TP が当たった玉は on_new_bar 時点で flat となり同足で新規
            #   ペンディングを設置できる（2603-01: SL@バー open→同足で次玉設置を再現）。後段の
            #   ティックループは open tick を再評価するが、生存玉は同クォートで冪等（二重決済なし）。
            if (
                pending_mode and not halted and open_trades and bar_ticks
                and not session_gate.is_closed(bar_index)
            ):
                o_price = bar_ticks[0][0]
                oq_bid, oq_ask = PendingLifecycleEngine.tick_quote(
                    o_price, spread=bar.spread, point_size=spec.point_size
                )
                kept_open: list[_OpenTrade] = []
                for ot in open_trades:
                    sltp_price = oq_ask if ot.position.side == "sell" else oq_bid
                    reason = check_sltp_hit_at_tick(
                        ot.position, price=sltp_price, sl=ot.sl, tp=ot.tp,
                        sltp_tie=features.sltp_tie,
                    )
                    if reason is None:
                        kept_open.append(ot)
                        continue
                    exit_price = ot.sl if reason == "sl" else ot.tp
                    close_trade(
                        ot, exit_time=bar.time, exit_price=exit_price, exit_reason=reason,
                    )
                open_trades = kept_open

            # ★ペンディング経路: 前足から残存するペンディングは、実 MT5 ではサーバが当該バーの
            #   open tick で評価してから EA が OnTick で削除する。よって on_new_bar より先に、
            #   残存ペンディングを open クォートで 1 回トリガ評価する（2603-01: bar 23:49 の
            #   sell_limit が bar 23:50 の open=約定価格に達し open tick で約定する例を再現）。
            #   約定玉は opened_tick_ordinal=0 とし、後段ティックループでは open tick（序数0）の
            #   SL/TP 判定を抑止する（約定ティック自身では決済しない）。未約定分は直後の
            #   on_new_bar で EA が削除する（resting_pending を空へ再設定）。flat 時のみ残存
            #   ペンディングを持つ（保有中は signal==current で再設置されないため）。
            if (
                pending_mode
                and not halted
                and resting_pending
                and bar_ticks
                and not session_gate.is_closed(bar_index)
            ):
                ro_price = bar_ticks[0][0]
                rq_bid, rq_ask = PendingLifecycleEngine.tick_quote(
                    ro_price, spread=bar.spread, point_size=spec.point_size
                )
                # トリガ評価 + OCO 判定はエンジンへ委譲（純ロジック）。約定 Position の口座
                #   反映（open_positions/margin/open_trades）は口座アクターとして本 Interactor
                #   が担う。走査順＝反映順のため byte-identical（opened_tick_ordinal=0）。
                #   OCO: 同一評価点で trigger した stop は全て約定（実 MT5 hedging・広 spread/doji
                #   で両建て成立＝2604-02 実証）。約定が起きたら非約定分のみ EA が取消す。
                filled, carried = PendingLifecycleEngine.evaluate_triggers(
                    resting_pending, bid=rq_bid, ask=rq_ask, oco=pending_oco
                )
                for order, pos in filled:
                    account.open_positions.append(pos)
                    account.margin += pos.required_margin(request.account.leverage, contract_size)
                    open_trades.append(
                        _OpenTrade(
                            position=pos,
                            sl=order.sl,
                            tp=order.tp,
                            entry_time=bar.time,
                            entry_price=pos.entry_price,
                            opened_bar_index=bar_index,
                            skip_entry_bar=False,
                            opened_tick_ordinal=0,
                        )
                    )
                resting_pending = carried

            # D/E ★足境界のみ: 新規バーシグナル評価（ティックで呼ばない）。
            #   halt 後は発注しない（玉を増やさない）。open-tick SL/TP 後の保有で評価する。
            #   戦略の戻り値は admit_orders（受理の唯一の門・ISSUE-445 段階 3-C）を通す。
            orders = (
                []
                if halted
                else admit_orders(
                    self._strategy.on_new_bar(bar_index, self._indicators, account) or [],
                    spec,
                )
            )
            # 注文方式で経路を分ける（kind="market" は足境界の成行・既存経路／指値・逆指値の
            #   ペンディングは足途中ティックでトリガ評価する別経路）。pending EA は kind が
            #   pending 4 種のみを返し market_orders は空＝既存成行経路は不活性。
            market_orders = [o for o in orders if o.kind == "market"]
            pending_orders = [o for o in orders if o.kind != "market"]

            # F ★足境界・バー open クォートで成行約定（bar-mode と同一: derive_quotes）。
            #   実 MT5 は新規バーの成行を「バー open のクォート」（買い=open+spread×point、
            #   売り=open）で約定し、ティックは含み損/SL-TP/stop-out の評価にのみ用いる
            #   （bar-mode 突合 2025-01 と同一の建値ルール）。ティック 0 件足では発注しない。
            #   市場閉鎖バー（closed_bars）も新規成行を約定しない（ドテン反転の reverse
            #   決済も含め全約定を抑止。保有不変＝戦略が次の開場バーで自動再発注）。
            if bar_ticks and market_orders and not session_gate.is_closed(bar_index):
                bid0, ask0, fill_spread, fill_point = derive_quotes(
                    bar,
                    entry_price_basis=features.entry_price_basis,
                    point_size=spec.point_size,
                )
                for order in market_orders:
                    # 反対サイドの保有玉があれば bar open クォートで reverse 決済。
                    reverse_kept: list[_OpenTrade] = []
                    for ot in open_trades:
                        if ot.position.side != order.side:
                            close_price = close_price_for(
                                ot.position.side, bid=bid0, ask=ask0
                            )
                            close_trade(
                                ot,
                                exit_time=bar.time,
                                exit_price=close_price,
                                exit_reason="reverse",
                            )
                        else:
                            reverse_kept.append(ot)
                    open_trades = reverse_kept

                    position = fill_market_order(
                        order, bid=bid0, ask=ask0, spread=fill_spread, point_size=fill_point
                    )
                    account.open_positions.append(position)
                    account.margin += position.required_margin(
                        request.account.leverage, contract_size
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

            # ★ペンディング（指値/逆指値）の bar open 処理（PROCESS §4.2 拡張）。
            #   実 EA は毎バー自ペンディングを取消し（DeleteOwnPendingOrders）最新シグナルで再設置
            #   する。よって on_new_bar 時点で残存ペンディングを破棄（resting_pending を空へ）し、
            #   今足のシグナルが返したペンディングを新たな resting_pending とする。逆方向保有時は
            #   bar open クォートで成行ドテン決済してから設置する（原典 PositionClose→OpenPending）。
            #   市場閉鎖バー（closed_bars）は発注も逆決済もしない（[market closed]）。pending_mode で
            #   なくても resting_pending は空のまま（pending_orders は market EA では空）。
            #   pending_persistent では resting をバー境界でリセットしない（約定まで保持し、
            #   再アームは足途中ティックの on_tick が担う＝実 MT5 の OnTick 即時再設置に整合）。
            if pending_mode and not pending_persistent:
                resting_pending = []  # EA が自ペンディングを削除（未約定の残存分を破棄）
            if bar_ticks and pending_orders and not session_gate.is_closed(bar_index):
                pbid0, pask0, _, _ = derive_quotes(
                    bar,
                    entry_price_basis=features.entry_price_basis,
                    point_size=spec.point_size,
                )
                for order in pending_orders:
                    reverse_kept2: list[_OpenTrade] = []
                    for ot in open_trades:
                        if ot.position.side != order.side:
                            close_price = close_price_for(
                                ot.position.side, bid=pbid0, ask=pask0
                            )
                            close_trade(
                                ot,
                                exit_time=bar.time,
                                exit_price=close_price,
                                exit_reason="reverse",
                            )
                        else:
                            reverse_kept2.append(ot)
                    open_trades = reverse_kept2
                    resting_pending.append(order)

            saw_tick = False
            # 閉鎖バー（pre-open 01:00 / 日次クローズ 23:59 等）の取引可否（トレードセッション）。
            #   実 MT5 のセッション規約: 顧客注文（新規約定・ペンディング fill・SL/TP）はトレード
            #   セッション外では実行しない。一方 stop-out（ブローカーのリスク清算）と含み損評価は
            #   セッション外でも行う（2603 で 01:00 pre-open の stop-out が MT5 と一致・2604-02 で
            #   01:00 の SL/TP は MT5 で発火しないことを実証）。よって閉鎖バーでは SL/TP・約定・
            #   再アームのみ抑止し、equity 更新と stop-out は継続する。
            bar_closed = session_gate.is_closed(bar_index)
            for tick_ordinal, tick in enumerate(bar_ticks):
                price, bid, ask, _tick_time = tick
                saw_tick = True
                last_bid, last_ask = bid, ask
                # ペンディング経路は実 MT5 OHLC のクォート規約（bid=ティック価格＝OHLC /
                #   ask=bid+spread×point）を採る。SL/TP は決済サイドのクォートで判定する
                #   （sell 保有は Ask で SL/TP・buy 保有は Bid で SL/TP＝実 MT5 整合。2603-01:
                #   sell の SL は high tick の Ask=high+spread×point で発火）。非ペンディング
                #   （real_ticks）経路は従来どおり tick の price で判定し byte-identical を保つ。
                if pending_mode:
                    q_bid, q_ask = PendingLifecycleEngine.tick_quote(
                        price, spread=bar.spread, point_size=spec.point_size
                    )
                else:
                    q_bid = q_ask = None

                # H 保有玉 SL/TP を到達ティック価格で判定（fill_delay=次tick: 発注足は監視外）。
                #   skip_entry_bar=True（成行）は建てた足のティックを監視外とし従来不変。
                #   ペンディング約定玉（skip_entry_bar=False）は「約定ティックより後」の同足ティック
                #   から監視する（opened_tick_ordinal 以下のティックは約定ティック自身ゆえ抑止）。
                still_open: list[_OpenTrade] = []
                for ot in open_trades:
                    if bar_closed:
                        still_open.append(ot)  # 閉鎖バーは SL/TP 監視外（顧客注文＝セッション外不可）
                        continue
                    if ot.opened_bar_index == bar_index:
                        if ot.skip_entry_bar:
                            still_open.append(ot)  # 建てた足は監視外（成行）
                            continue
                        if tick_ordinal <= ot.opened_tick_ordinal:
                            still_open.append(ot)  # 約定ティック以前は監視外（ペンディング）
                            continue
                    if pending_mode:
                        sltp_price = q_ask if ot.position.side == "sell" else q_bid
                    else:
                        sltp_price = price
                    reason = check_sltp_hit_at_tick(
                        ot.position,
                        price=sltp_price,
                        sl=ot.sl,
                        tp=ot.tp,
                        sltp_tie=features.sltp_tie,
                    )
                    if reason is None:
                        still_open.append(ot)
                        continue
                    exit_price = ot.sl if reason == "sl" else ot.tp
                    close_trade(
                        ot,
                        exit_time=bar.time,
                        exit_price=exit_price,
                        exit_reason=reason,
                    )
                open_trades = still_open

                # B4 建玉変更（トレーリング FR-07・部分決済 FR-08）を hit 判定（H）の後・I の前に
                #   適用する（Phase 7・忠実順序: on_tick 等の EA 動作帯・I の前）。既定 pm=None は
                #   素通り＝byte-identical。参照/決済価格は保有玉の決済価格（buy=Bid / sell=Ask＝
                #   close_price_for・floating 評価と同一基準）。pending_mode は q_bid/q_ask、
                #   real_ticks は tick の bid/ask を用いる。閉鎖バーは EA 動作外で適用しない。
                if pm is not None and open_trades and not bar_closed:
                    mb = q_bid if pending_mode else bid
                    ma = q_ask if pending_mode else ask
                    for ot in list(open_trades):
                        exit_px = close_price_for(ot.position.side, bid=mb, ask=ma)
                        directive = pm.evaluate(
                            ot=ot, ref_price=exit_px, granularity="tick", account=account
                        )
                        self._apply_directive(
                            directive, ot, close_trade=close_trade,
                            exit_time=bar.time, exit_price=exit_px,
                        )

                # ★armed ペンディングのトリガ評価（約定価格＝注文価格・スリッページ0）。
                #   クォート規約は derive_quotes と対称に bid=ティック価格 / ask=bid+spread×point
                #   （centered tick の bid/ask は使わない）。実 MT5 はペンディングが約定した
                #   コントロールポイント（ティック）自身では SL/TP を判定せず、後続ティック以降で
                #   監視する（2603-01 journal で実証: 約定@H tick→SL は後続 C tick で発火＝同足、
                #   後続足が SL/TP 未達なら次足へ持ち越し）。よって同ティック判定はせず、
                #   skip_entry_bar=False で open_trades へ積み「約定ティックより後」のみ監視させる。
                if resting_pending and not session_gate.is_closed(bar_index):
                    # トリガ評価 + OCO 判定はエンジンへ委譲（純ロジック）。約定 Position の
                    #   口座反映（open_positions/margin/open_trades）は本 Interactor が担う。
                    #   走査順＝反映順のため byte-identical（opened_tick_ordinal=tick_ordinal）。
                    #   OCO: 同一ティックで trigger した stop は全約定（実 MT5 hedging・広 spread/
                    #   doji で両建て成立）。約定が起きたら非約定分のみ EA が取消す。
                    filled, carried = PendingLifecycleEngine.evaluate_triggers(
                        resting_pending, bid=q_bid, ask=q_ask, oco=pending_oco
                    )
                    for order, pos in filled:
                        account.open_positions.append(pos)
                        account.margin += pos.required_margin(
                            request.account.leverage, contract_size
                        )
                        open_trades.append(
                            _OpenTrade(
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
                    resting_pending = carried

                # ★ペンディング持続モードの足途中再アーム（ISSUE-024・実 MT5 OnTick 相当）。
                #   SL/TP 決済直後など「保有0・resting 0」のティックで、当該ティッククォート
                #   （bid=price / ask=price+spread×point）で即ペンディングを再装填する。バー境界の
                #   on_new_bar ではなく決済が起きた実ティックの bid/ask を使うため、再アーム価格が
                #   実 MT5 と一致する（journal 検証: bar 01:01 の SL@L 制御点 Bid=52939.8→
                #   BuyStop=52954.8）。置いたティック自身では fill 判定せず次ティック以降で評価する。
                if (
                    pending_persistent
                    and pending_mode
                    and not halted
                    and not open_trades
                    and not resting_pending
                    and not session_gate.is_closed(bar_index)
                ):
                    # 再アームも発注であり、受理の門（admit_orders）を通す。
                    rearm = admit_orders(
                        self._strategy.on_tick(bar_index, q_bid, q_ask, account) or [],
                        spec,
                    )
                    if rearm:
                        resting_pending = list(rearm)

                # I ティック評価価格で含み損更新 → equity 記録 → stop-out 判定。
                #   ペンディング経路は MT5 OHLC クォート（bid=price / ask=price+spread×point）で
                #   評価し floating_pnl_basis="bid_ask" と整合させる（centered tick は使わない）。
                eval_bid = q_bid if pending_mode else bid
                eval_ask = q_ask if pending_mode else ask
                account.update_floating_pnl_at(bid=eval_bid, ask=eval_ask)
                equity_curve.append(account.equity)
                # stop-out 判定の証拠金維持率。hedged_margin では反対玉を相殺し「買い計・売り計の
                #   大きい側」を実効証拠金とする（同量両建ては実質ノーマージン＝stop-out しない・
                #   実 MT5 hedging に整合）。既定は account.margin_level()（従来の単純加算）で不変。
                margin_level = account.margin_level()
                if hedged_margin and open_trades:
                    # 実効証拠金算出は口座不変ルールとして Account が所有する（ISSUE-094）。
                    #   保有列は account.open_positions と open_trades が常時 lockstep のため
                    #   走査対象・順序・式が inline 版と同一＝byte-identical。
                    margin_level = account.hedged_margin_level(
                        leverage=request.account.leverage, contract_size=contract_size
                    )
                if margin_level < request.account.stop_out_level:
                    self._apply_stop_out(
                        policy=state.stop_out_policy,
                        open_trades=open_trades,
                        close_trade=close_trade,
                        account=account,
                        stop_out_level=request.account.stop_out_level,
                        bar=bar,
                        bar_index=bar_index,
                        bid=eval_bid,
                        ask=eval_ask,
                        where=_STOP_OUT_AT_EVALUATION,
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

        # ★ペンディング経路: テスト期間終了時に残存する建玉を最終足の close クォートで強制
        #   決済する（実 MT5 はテスト終了時に未決済ポジションを最終価格で清算する。2603-01:
        #   最終 buy を最終足 23:59 の close=51029.8 で決済し profit+20）。buy 決済=Bid=close /
        #   sell 決済=Ask=close+spread×point（close_price_for）。pending_mode 限定で既定経路は不変。
        if pending_mode and open_trades and bars:
            fbar = bars[-1]
            f_bid = fbar.close
            f_ask = fbar.close + fbar.spread * spec.point_size
            for ot in open_trades:
                close_price = close_price_for(ot.position.side, bid=f_bid, ask=f_ask)
                close_trade(
                    ot,
                    exit_time=fbar.time,
                    exit_price=close_price,
                    exit_reason="end_of_test",
                )
            open_trades = []

        return self._finish_run(
            trades=trades,
            deals=deals,
            balance_curve=balance_curve,
            equity_curve=equity_curve,
            initial_deposit=request.account.initial_deposit,
        )
