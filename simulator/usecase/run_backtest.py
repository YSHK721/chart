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
from simulator.usecase.bar_schedule import BarSchedule
from simulator.usecase.compute_stats import compute_stats
from simulator.usecase.evaluation_point import BAR_GRANULARITY, TICK_GRANULARITY
from simulator.usecase.models import AccountSpec, BacktestResult
from simulator.usecase.pending_lifecycle import PendingLifecycleEngine
from simulator.usecase.ports import RunBacktestInputBoundary
from simulator.usecase.run_features import RunFeatures
from simulator.usecase.session_gate import SessionGate
from simulator.usecase.stop_out_policy import StopOutContext, resolve_stop_out_policy
from simulator.usecase.tick_schedule import TickSchedule


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

# バー open の pseudo-tick を評価するときの約定価格基準（実 bid/ask 固定）。
_BAR_OPEN_PSEUDO_TICK_BASIS = "current_open"

# 評価粒度の名前は evaluation_point が単一ソース（建玉変更へもこの名前で伝える）。


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
    leverage: float
    stop_out_level: float
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
    # 残存ペンディング（指値/逆指値）。前足で設置され未約定のまま次足へ持ち越し、
    # 評価点ごとにトリガ評価される。ペンディング経路以外では常に空で挙動不変。
    resting_pending: list
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
        # 約定損益の口座通貨丸め桁（run の準備段が config から設定する）。
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
            leverage=request.account.leverage,
            stop_out_level=request.account.stop_out_level,
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
            resting_pending=[],
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
        """1 run を実行する（UC-001 の入口）。

        粒度（バーで評価するかティックで評価するか）はスケジュールが引き受ける。入口は
        run のスイッチを 1 度だけ読み、粒度に合うスケジュールを組んで本体へ渡す。
        """
        features = RunFeatures.of(request.config)
        return self._run(request, features, self._make_schedule(request, features))

    def _make_schedule(
        self, request: RunBacktestRequest, features: RunFeatures
    ) -> Any:
        """run の粒度に合う評価スケジュールを組む（run につき 1 つ）。

        実ティックを消費する run と、ペンディングのライフサイクルを回す run は、足の
        途中に評価点を要する（前者はティックごとの値洗いのため、後者はトリガ評価のため）。
        それ以外は 1 バー 1 点で足りる。
        """
        point_size = request.symbol_spec.point_size
        if features.tick_model == "real_ticks" or features.pending_lifecycle:
            return TickSchedule(
                tick_model=self._tick_model,
                pending_lifecycle=features.pending_lifecycle,
                point_size=point_size,
            )
        return BarSchedule(
            floating_pnl_basis=features.floating_pnl_basis, point_size=point_size
        )

    def _run(
        self, request: RunBacktestRequest, features: RunFeatures, schedule: Any
    ) -> BacktestResult:
        """バックテストの本体（PROCESS §2 A〜I）。粒度を問わず本メソッド 1 本で走る。

        1 バーの進み方:
            C   確定足の指標を更新する（足単位・粒度に依らない）
            -   ウォームアップ / プライム区間はここで打ち切る（指標の収束だけを行う）
            -   当該バーの評価点を取り出す（ティック列の取得もここで 1 回きり）
            I'  バー open の stop-out 先行判定（**バー粒度のみ**・後述）
            -   ペンディング経路のサーバ処理（バー先頭の点で SL/TP → 残存トリガ）
            D/E 新規バーのシグナル評価（足境界のみ。足の途中では呼ばない）
            F   成行約定（足境界のバー open クォート）
            -   ペンディングの設置（EA が毎バー貼り替える／持続モードでは残す）
            H→建玉変更→I を評価点ごとに（1 点ぶんは _evaluate_point が担う）

        バー open の stop-out 先行判定がバー粒度だけの規則である理由:
            実 MT5 の 1 分足 OHLC は O→H→L→C の最初の pseudo-tick（open）で証拠金を
            評価する。バー粒度の run には足の途中の評価点が無いので、この pseudo-tick を
            明示的に補う必要がある。ティック粒度の run では **open のティック自体が評価点**
            なので、補えば同じ瞬間を二度評価することになる（現に every-tick 経路はこの
            設定を見ていない）。

        ペンディング注文がティック粒度だけの概念である理由:
            指値・逆指値は「足の途中で価格が水準に触れたら約定する」注文であり、引く機会
            （評価点）が足の途中に無ければ意味を持たない。バー粒度の run では発注方式で
            分けず、すべて足境界の成行として扱う（現状の契約）。
        """
        state = self._begin_run(request, features)
        bars = state.bars
        spec = state.spec
        account = state.account
        session_gate = state.session_gate
        open_trades = state.open_trades
        halted = state.halted
        trading_start = state.trading_start
        prime_first = state.prime_first
        primed_done = state.primed_done
        # 粒度はスケジュール自身が名乗る（実行経路で config の文字列を読み直さない）。
        tick_granularity = schedule.id == TICK_GRANULARITY
        # ペンディングのライフサイクル（EA が毎バー貼り替えるか・約定まで持続させるか）。
        pending_mode = features.pending_lifecycle
        pending_persistent = features.pending_persistent
        # バー open の stop-out 先行判定はバー粒度の規則（上記 docstring 参照）。
        stop_out_at_open = features.stop_out_at_open and not tick_granularity
        # 前足の終値（ティックを合成する実装が要求する。バー粒度では使われない）。
        prev_close: "float | None" = None

        for bar_index, bar in enumerate(bars):
            # C 指標値の取得（前計算系列から現足インデックスを引く）
            self._indicators.update(bar_index)
            # warmup 区間（bar.time < trading_start）は指標 seed 収束のみを行い、トレード
            # 評価・約定・SL/TP 監視・equity 記録をすべてスキップする（config-gated）。
            if trading_start is not None and bar.time < trading_start:
                prev_close = bar.close
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
                prev_close = bar.close
                continue

            # 当該足の評価点を materialize する。バー先頭の先行処理と約定可否の判断が
            #   「このバーに評価点が在るか／先頭の点がどのクォートか」を要するため、点は
            #   バーの先頭で 1 度だけ取り出す（ティック列の取得もここで 1 回きり）。
            points = list(schedule.points(bar_index, bar, prev_close))
            # 実際に価格が成立した点が在るバーか（ティック 0 件バーは合成点 1 つだけ）。
            #   実 MT5 every-tick は新規バーを「最初のティック」で検知するため、ティック
            #   0 件足では新規バーを検知せず発注しない（次足へ持ち越さない）。
            has_points = bool(points) and not points[0].is_synthetic_bar_point
            bar_closed = session_gate.is_closed(bar_index)

            # I' ★バー open での stop-out 先行判定（バー粒度・config gated）。週末ギャップ等で
            #   open が割れた保有玉は「バー open クォート」で強制決済される（買い=Bid=open /
            #   売り=Ask=open+spread×point）。後段の close 基準判定（I）は残すため、open が
            #   割れず bar 内で割れる場合は従来どおり close で決済する。既定 False で本ブロックは
            #   不活性（ISSUE-022）。open 評価は floating_pnl_basis を参照せず実 bid/ask 固定。
            if stop_out_at_open and open_trades and not halted:
                o_bid, o_ask, _, _ = derive_quotes(
                    bar,
                    entry_price_basis=_BAR_OPEN_PSEUDO_TICK_BASIS,
                    point_size=spec.point_size,
                )
                account.update_floating_pnl_at(bid=o_bid, ask=o_ask)
                if account.margin_level() < state.stop_out_level:
                    self._apply_stop_out(
                        policy=state.stop_out_policy,
                        open_trades=open_trades,
                        close_trade=state.close_trade,
                        account=account,
                        stop_out_level=state.stop_out_level,
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
                    #   後段 I で close 基準により記録する）。
                    nb_bid, nb_ask = resolve_eval_quote(
                        bar,
                        basis=state.floating_pnl_basis,
                        point_size=spec.point_size,
                    )
                    account.update_floating_pnl_at(bid=nb_bid, ask=nb_ask)

            # ★ペンディング経路のサーバ処理。実 MT5 はバー先頭ティック(open)で、EA の
            #   OnTick を呼ぶ**前に**保有玉の SL/TP を処理し、残存ペンディングを評価する。
            #   open で SL/TP が当たった玉は on_new_bar 時点で flat となり、同足で新規
            #   ペンディングを設置できる（2603-01: SL@バー open→同足で次玉設置を再現）。
            #   後段の評価点ループは open の点を再評価するが、生存玉は同クォートで冪等。
            if pending_mode and not halted and has_points and not bar_closed:
                if open_trades:
                    # この時点で当該バーに建てた玉は無いので、建て足の監視抑止は空振りする。
                    open_trades = self._check_sltp_hits(
                        state, points[0], open_trades, closed=False
                    )
                if state.resting_pending:
                    # 先頭の点（バー open ティック）で 1 回だけトリガ評価する。約定玉は
                    #   序数 0 で積まれ、後段の点は open の判定を抑止する。
                    self._trigger_resting_pending(state, open_trades, points[0])

            # D/E ★足境界のみ: 新規バーのシグナル評価（足の途中では呼ばない）。
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
            # 市場閉鎖バーは新規注文を一切通さない（ドテン反転の reverse 決済も含む）。
            #   on_new_bar は評価済＝保有不変のため、戦略（保有側基準の level-trigger）が
            #   次の開場バーで自動再発注し、実 MT5 の fail→retry→開場約定を再現する。
            #   SL/TP(H)・equity/stop-out(I) は閉鎖バーでも従来どおり評価する。
            if bar_closed:
                orders = []
            if tick_granularity:
                # 指値・逆指値は足途中の評価点でトリガを引く別経路へ回す。
                market_orders = [o for o in orders if o.kind == "market"]
                pending_orders = [o for o in orders if o.kind != "market"]
            else:
                # バー粒度は足途中の評価点を持たないのでペンディングを引く機会が無い。
                #   発注方式で分けず、すべて足境界の成行として扱う（現状の契約）。
                market_orders, pending_orders = orders, []

            # F 発注（成行約定）。反対玉の reverse 決済 → 建玉 → 口座反映を注文ごとに
            #   完了させる（走査順＝反映順）。建値の導出も約定段が所有する。実 MT5 は
            #   新規バーの成行を「バー open のクォート」で約定し、足途中の点は含み損・
            #   SL/TP・stop-out の評価にのみ用いる。
            if has_points and market_orders:
                open_trades = self._fill_market_orders(
                    state, market_orders, open_trades, bar=bar, bar_index=bar_index
                )

            # ★ペンディングの設置（PROCESS §4.2 拡張）。実 EA は毎バー自ペンディングを
            #   取消し、最新シグナルで再設置する。逆方向を保有していれば bar open クォートで
            #   成行ドテン決済してから設置する（原典 PositionClose→OpenPending）。
            #   持続モードでは貼り替えず約定まで保持する（再アームは足途中の点が担う）。
            if pending_mode and not pending_persistent:
                state.resting_pending = []
            if has_points and pending_orders:
                pbid0, pask0, _, _ = derive_quotes(
                    bar,
                    entry_price_basis=features.entry_price_basis,
                    point_size=spec.point_size,
                )
                for order in pending_orders:
                    open_trades = self._close_opposite_positions(
                        state, open_trades, order, bar=bar, bid=pbid0, ask=pask0
                    )
                    state.resting_pending.append(order)

            # H → 建玉変更 → ペンディング → I を評価点ごとに行う。「どこで評価するか」は
            #   スケジュールが決め、「評価点で何をするか」は _evaluate_point が持つ。
            for point in points:
                open_trades, halted = self._evaluate_point(
                    state, point, open_trades, halted
                )

            prev_close = bar.close

        # ★ペンディング経路: テスト期間終了時に残る建玉を最終足の close クォートで清算する
        #   （実 MT5 はテスト終了時に未決済ポジションを最終価格で決済する。2603-01: 最終 buy を
        #   最終足 23:59 の close=51029.8 で決済し profit+20）。買い決済=Bid=close /
        #   売り決済=Ask=close+spread×point。ペンディング経路限定で既定経路は不変。
        if pending_mode and open_trades and bars:
            fbar = bars[-1]
            f_bid = fbar.close
            f_ask = fbar.close + fbar.spread * spec.point_size
            for ot in open_trades:
                close_price = close_price_for(ot.position.side, bid=f_bid, ask=f_ask)
                state.close_trade(
                    ot,
                    exit_time=fbar.time,
                    exit_price=close_price,
                    exit_reason="end_of_test",
                )
            open_trades = []

        # OnDeinit 集計
        return self._finish_run(
            trades=state.trades,
            deals=state.deals,
            balance_curve=state.balance_curve,
            equity_curve=state.equity_curve,
            initial_deposit=request.account.initial_deposit,
        )

    def _close_opposite_positions(
        self,
        state: _RunState,
        open_trades: list,
        order: Any,
        *,
        bar: Any,
        bid: float,
        ask: float,
    ) -> list:
        """発注と反対サイドの保有玉を reverse 決済し、残る保有列を返す（PROCESS §6）。

        走査順をそのまま残す（残す玉は `kept` へ現れた順に積む）。保有列の並びは証拠金の
        按分解放と強制決済の走査順に効くため、ここで並びが変わると確定トレードの並びが動く。

        決済価格は `close_price_for` が約定価格ルール（買い決済=Bid / 売り決済=Ask）で
        一意に決める。成行・ペンディング設置の双方が同じ規則で反対玉を畳む。
        """
        kept: "list[_OpenTrade]" = []
        for ot in open_trades:
            if ot.position.side != order.side:
                close_price = close_price_for(ot.position.side, bid=bid, ask=ask)
                state.close_trade(
                    ot,
                    exit_time=bar.time,
                    exit_price=close_price,
                    exit_reason="reverse",
                )
            else:
                kept.append(ot)
        return kept

    def _fill_market_orders(
        self,
        state: _RunState,
        orders: list,
        open_trades: list,
        *,
        bar: Any,
        bar_index: int,
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
            entry_price_basis=state.features.entry_price_basis,
            point_size=state.spec.point_size,
        )
        for order in orders:
            open_trades = self._close_opposite_positions(
                state, open_trades, order, bar=bar, bid=bid, ask=ask
            )
            position = fill_market_order(
                order, bid=bid, ask=ask, spread=fill_spread, point_size=fill_point
            )
            state.account.open_positions.append(position)
            state.account.margin += position.required_margin(
                state.leverage, state.contract_size
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
        return open_trades

    def _admit_pending_fills(
        self,
        state: _RunState,
        open_trades: list,
        filled: list,
        *,
        bar: Any,
        bar_index: int,
        tick_ordinal: int,
    ) -> None:
        """トリガしたペンディングの約定を口座へ反映する（走査順＝反映順）。

        トリガ判定と OCO はエンジン（PendingLifecycleEngine）が純ロジックとして持ち、
        約定 Position の口座反映（保有列・証拠金・保有玉）は口座アクターである本
        Interactor が担う。約定玉は `skip_entry_bar=False`・`opened_tick_ordinal` 付きで
        積み、「約定したティックより後」からのみ SL/TP を監視させる（約定したティック
        自身では決済しない＝実 MT5 server 整合）。
        """
        for order, pos in filled:
            state.account.open_positions.append(pos)
            state.account.margin += pos.required_margin(
                state.leverage, state.contract_size
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

    def _trigger_resting_pending(
        self, state: _RunState, open_trades: list, point: Any
    ) -> None:
        """残存ペンディングを当該評価点のクォートでトリガ評価する。

        OCO: 同一評価点で trigger した注文は（OCO 無効なら）すべて約定する。実 MT5 の
        hedging 口座では広い spread や doji で両建てが成立する（2604-02 実証）。約定が
        起きたら、非約定分は EA が取り消す。
        """
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            state.resting_pending,
            bid=point.eval_bid,
            ask=point.eval_ask,
            oco=state.features.pending_oco,
        )
        self._admit_pending_fills(
            state,
            open_trades,
            filled,
            bar=point.bar,
            bar_index=point.bar_index,
            tick_ordinal=point.tick_ordinal,
        )
        state.resting_pending = carried

    def _check_sltp_hits(
        self, state: _RunState, point: Any, open_trades: list, *, closed: bool
    ) -> list:
        """評価点 1 つで保有玉の SL/TP 到達を判定し、残る保有列を返す（H 段の単一化）。

        監視しない玉が 2 種類ある:

          * 市場閉鎖バーの全玉。SL/TP は顧客注文であり、トレードセッション外では
            執行されない（stop-out はブローカーのリスク清算なので閉鎖中も続く）。
          * 建てた足の玉のうち、まだ「約定の次」に達していないもの。成行は足全体を
            監視外にし（fill_delay=次tick）、ペンディング約定は約定ティックより後から
            監視する（約定したティック自身では決済しない＝実 MT5 server 整合）。

        到達を見る価格範囲は点がサイドごとに持つ。ペンディング経路だけが買い（Bid）と
        売り（Ask）で違う範囲になるためである。
        """
        if closed:
            return open_trades
        still_open: "list[_OpenTrade]" = []
        for ot in open_trades:
            if ot.opened_bar_index == point.bar_index:
                if ot.skip_entry_bar:
                    still_open.append(ot)  # 建てた足は監視外（成行）
                    continue
                if point.tick_ordinal <= ot.opened_tick_ordinal:
                    still_open.append(ot)  # 約定ティック以前は監視外（ペンディング）
                    continue
            if ot.position.side == "buy":
                hit_high, hit_low = point.hit_buy_high, point.hit_buy_low
            else:
                hit_high, hit_low = point.hit_sell_high, point.hit_sell_low
            reason = check_sltp_hit(
                ot.position,
                high=hit_high,
                low=hit_low,
                sl=ot.sl,
                tp=ot.tp,
                sltp_tie=state.features.sltp_tie,
            )
            if reason is None:
                still_open.append(ot)
                continue
            exit_price = ot.sl if reason == "sl" else ot.tp
            state.close_trade(
                ot,
                exit_time=point.bar.time,
                exit_price=exit_price,
                exit_reason=reason,
            )
        return still_open

    def _evaluate_point(
        self, state: _RunState, point: Any, open_trades: list, halted: bool
    ) -> "tuple[list, bool]":
        """1 評価点で行うことすべて（両粒度で共有する唯一の手続き）。

        順序は実 MT5 の成立順に従う: サーバが SL/TP を執行し（H）、EA が建玉を触り
        （建玉変更）、最後に口座が再評価される（I）。粒度による違いは点の中身
        （価格の取り方・粒度名・ティック序数）に畳み込まれており、手続き自体は 1 つである。

        両建ての証拠金相殺はティック粒度の規則なので、粒度から決める（バー評価は設定が
        立っていても適用しない＝現状の契約）。

        事後条件: `(更新後の保有列, halt したか)`。
        """
        if point.is_synthetic_bar_point:
            # ティックが 1 本も無いバーの点。そのバーで実際に成立した価格が無いので、
            #   SL/TP も stop-out も判定しない（判定する材料が無い）。保有玉が在るときだけ、
            #   持ち越したクォートで値洗いして equity へ 1 点残す。保有が無ければ評価対象
            #   自体が無いので何も記録しない。
            if not open_trades:
                return open_trades, halted
            state.account.update_floating_pnl_at(bid=point.eval_bid, ask=point.eval_ask)
            state.equity_curve.append(state.account.equity)
            return open_trades, halted

        closed = state.session_gate.is_closed(point.bar_index)
        open_trades = self._check_sltp_hits(state, point, open_trades, closed=closed)
        if self._position_manager is not None and open_trades and not closed:
            self._apply_position_directives(
                state,
                open_trades,
                bar=point.bar,
                granularity=point.granularity,
                ref_buy=point.pm_ref_buy,
                ref_sell=point.pm_ref_sell,
            )
        # 残存ペンディングのトリガ評価。約定価格＝注文価格（スリッページ 0）。
        #   バー粒度では残存が常に空なので、この段は素通りする。
        if state.resting_pending and not closed:
            self._trigger_resting_pending(state, open_trades, point)
        # ペンディング持続モードの足途中再アーム（実 MT5 の OnTick 即時再設置に相当）。
        #   SL/TP 決済直後など「保有 0・残存 0」の点で、その点のクォートで即装填する。
        #   バー境界の on_new_bar ではなく決済が起きた実ティックのクォートを使うため、
        #   再アーム価格が実 MT5 と一致する。置いた点自身では約定判定しない（次の点から）。
        if (
            state.features.pending_persistent
            and state.features.pending_lifecycle
            and not halted
            and not open_trades
            and not state.resting_pending
            and not closed
        ):
            # 再アームも発注であり、受理の門（admit_orders）を通す。
            rearm = admit_orders(
                self._strategy.on_tick(
                    point.bar_index, point.eval_bid, point.eval_ask, state.account
                )
                or [],
                state.spec,
            )
            if rearm:
                state.resting_pending = list(rearm)
        return self._settle_evaluation_point(
            state,
            open_trades,
            halted,
            bar=point.bar,
            bar_index=point.bar_index,
            eval_bid=point.eval_bid,
            eval_ask=point.eval_ask,
            hedged=(
                state.features.hedged_margin
                and point.granularity == TICK_GRANULARITY
            ),
        )

    def _apply_position_directives(
        self,
        state: _RunState,
        open_trades: list,
        *,
        bar: Any,
        granularity: str,
        ref_buy: float,
        ref_sell: float,
    ) -> None:
        """保有玉すべてに建玉変更を適用する（両実行経路で同型だった B2/B4 の単一化）。

        呼ばれる位置は hit 判定（H）の後・口座再評価（I）の前である（サーバ hit →
        EA 動作 → 口座再評価という実 MT5 の順序）。参照価格は玉のサイドだけで決まる
        ため、**評価点ごとに 2 つ（買い用・売り用）を先に解決して玉に配る**。玉ごとに
        引き直すと、同じ答えを玉の数だけ求める形（N+1）になり、玉が増えるほど捨てる
        計算が増える。出力は 1 ビットも変わらないので状態検証では落ちない類の浪費である。

        参照価格の意味は粒度で異なる（呼出側が決めて渡す）:
            バー粒度: トレーリング方向の到達価格（買い=high / 売り=low）。SL/TP の
                到達判定が high/low で touch を見るのと対称にする。
            ティック粒度: 保有玉の決済価格（買い=Bid / 売り=Ask）＝含み損評価と同一基準。

        `list(open_trades)` を走査するのは、適用中に部分決済が保有列を書き換えうるため
        （走査中の列を直接回すと取りこぼす）。
        """
        pm = self._position_manager
        for ot in list(open_trades):
            ref = ref_buy if ot.position.side == "buy" else ref_sell
            directive = pm.evaluate(
                ot=ot, ref_price=ref, granularity=granularity, account=state.account
            )
            self._apply_directive(
                directive,
                ot,
                close_trade=state.close_trade,
                exit_time=bar.time,
                exit_price=ref,
            )

    def _settle_evaluation_point(
        self,
        state: _RunState,
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
        account = state.account
        account.update_floating_pnl_at(bid=eval_bid, ask=eval_ask)
        state.equity_curve.append(account.equity)
        margin_level = account.margin_level()
        if hedged and open_trades:
            # 実効証拠金算出は口座不変ルールとして Account が所有する（ISSUE-094）。
            #   保有列は account.open_positions と open_trades が常時 lockstep のため
            #   走査対象・順序・式が inline 版と同一＝byte-identical。
            margin_level = account.hedged_margin_level(
                leverage=state.leverage, contract_size=state.contract_size
            )
        if margin_level < state.stop_out_level:
            self._apply_stop_out(
                policy=state.stop_out_policy,
                open_trades=open_trades,
                close_trade=state.close_trade,
                account=account,
                stop_out_level=state.stop_out_level,
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

        2 つのエンジンが並存していた頃、終了段は字句まで同一の写しが 2 つ在った。
        同じ処理が 2 箇所に在ると「片方だけが更新される」形の欠陥
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
