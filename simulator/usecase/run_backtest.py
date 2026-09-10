"""UC-001 RunBacktestInteractor（CLEAN_ARCH §3・PROCESS §2・DESIGN §6）。

config + Bar 列から確定トレード・deals・equity/balance 系列を構築し、compute_stats
（UC-002）で BacktestStats を算出して BacktestResult を返す。OnTick A〜I の処理順を
Interactor 内に閉じる（CLEAN_ARCH §3 注記）。

依存性注入: StrategyPort / IndicatorPort / TickModelPort を constructor で受ける（DIP）。
usecase は domain のみ依存（adapter/framework/main・pydantic を import しない）。

本モジュールが持つ責務は **run のライフサイクルだけ** である（ISSUE-502 段階 4A）:
準備段（`_begin_run`）・バーの進み方（`_run`）・評価点 1 つの成立順（`_evaluate_point`）・
終了段（`_finish_run`）。run のあいだに起きる個別の仕事は、それぞれ別の動機で改訂される
5 つの協働クラスが所有する（いずれも `_begin_run` で 1 度だけ組み、`_RunState` が持つ）:

===========================  ==============================================
協働クラス                    所有する規則
===========================  ==============================================
`TradeLedger`                決済の記帳（確定トレード・証拠金解放・Deal・balance）
`OrderExecutor`              約定執行（成行・reverse・ペンディング設置/トリガ/清算）
`SltpMonitor`                SL/TP 到達判定（監視外の 2 種類・同値裁定）
`MarginGuard`                含み損益の値洗い・equity 記録・証拠金割れの処理
`PositionDirectiveApplier`   建玉変更（トレーリング・部分決済）の忠実適用
===========================  ==============================================

なぜ分けたか: これらは**別々のアクター（改訂の動機）**に属する。ブローカーの証拠金規則が
変わる、実 MT5 の server 挙動に合わせて SL/TP の同値裁定が変わる、EA の建玉変更仕様が
変わる——どれも「run の進み方」とは無関係に来る。1 つのクラスに同居している限り、
どの改訂も同じ 940 行を開くことになり、変更の影響範囲が構造から読めない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simulator.domain.account import Account
from simulator.usecase._execution import admit_orders
from simulator.usecase.bar_schedule import BarSchedule
from simulator.usecase.compute_stats import compute_stats
from simulator.usecase.evaluation_point import TICK_GRANULARITY
from simulator.usecase.margin_guard import MarginGuard
from simulator.usecase.models import AccountSpec, BacktestResult
from simulator.usecase.order_execution import OrderExecutor
from simulator.usecase.ports import RunBacktestInputBoundary
from simulator.usecase.position_directives import PositionDirectiveApplier
from simulator.usecase.run_features import RunFeatures
from simulator.usecase.schedule_selection import requires_tick_granularity
from simulator.usecase.session_gate import SessionGate
from simulator.usecase.sltp_monitor import SltpMonitor
from simulator.usecase.stop_out_policy import resolve_stop_out_policy
from simulator.usecase.tick_schedule import TickSchedule
from simulator.usecase.trade_ledger import TradeLedger

# 評価粒度の名前は evaluation_point が単一ソース（建玉変更へもこの名前で伝える）。


@dataclass
class _RunState:
    """1 run の開始状態（両実行経路で完全一致していた準備段の産物）。

    保持するのは「run の間ずっと同じであり続けるもの」（銘柄仕様・口座・記録先・
    セッション判定・取引区間の設定・**協働クラス 5 つ**）と、「run の進行で書き換わる
    もの」の**初期値**（保有玉・halt・プライム消費済フラグ）である。

    なぜ 1 つの値にまとめるか: 準備段は 2 つのエンジンに字句レベルで書き写されており、
    片方だけに項目が足される形の食い違いを検定で捉えられなかった（両経路が同じ
    fixture を通らない限り数値の指紋に現れない）。組み立ての定義点を 1 つにすれば、
    その食い違い自体が起こり得なくなる。

    `close_trade` は決済呼び出しの**不変の文脈**（口座・記録先・銘柄仕様・レバレッジ）を
    束ねた呼び口である（ISSUE-308）。実体は `ledger.close` であり、2 つ目の実装は無い。

    **持たないもの**: 協働クラスを組むためだけに使った値（レバレッジ・ストップアウト水準・
    含み損益の評価基準・証拠金割れの方針）は本状態に載せない。これらは組み上がった時点で
    協働クラスの所有物であり、run はそれらを読まずに協働クラスへ問う。載せると
    「同じ値が 2 箇所に在る」形になり、片方だけが更新される欠陥を招く（ISSUE-502 段階 4A）。
    この不変条件は test_run_backtest_single_engine.py の構造検定
    （TestBothEnginesShareTheSetupStage の「run が読まない項目を持たない」検定）が
    構文木で機械的に施行する（宣言でなく検査で強制する）。
    """

    bars: list
    features: RunFeatures
    spec: Any
    contract_size: float
    account: Account
    session_gate: SessionGate
    # 協働クラス（run の準備段で 1 度だけ組む・run のあいだ同じ実体を使い回す）。
    ledger: TradeLedger
    executor: OrderExecutor
    sltp: SltpMonitor
    margin_guard: MarginGuard
    directives: PositionDirectiveApplier
    close_trade: Any
    # 観測口（実行トレース・DI・既定 None＝観測なし）。協働クラスと同じく **run につき
    #   1 度だけ**束ねる（`_run` の中で組まない）。読み手は必ず `state.tracer` である。
    tracer: Any
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
        schedule: Any = None,
        run_tracer: Any = None,
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
        # 評価スケジュール（DI・既定 None＝run ごとに粒度から組む＝既定経路 byte-identical）。
        #   注入する場合の契約: スケジュールは run のあいだの状態（ティック 0 件バーの
        #   持ち越しクォート等）を持ちうるため、**1 run につき 1 つ**でなければならない。
        #   同じ Interactor で複数 run を回す呼出側が 1 つのスケジュールを注入すると、
        #   run をまたいで状態が漏れる。既定（None）はその心配が無い——run ごとに組むため。
        self._schedule = schedule
        # 実行トレースの観測口（RunTracePort・DI・既定 None＝観測なし＝既定経路
        #   byte-identical）。None のときは呼出点を素通りする（`if state.tracer is not
        #   None` ゲート）。Null Object を採らない理由: 既定経路に 1 回の no-op 呼出も
        #   足さない（周囲の先例 `self._position_manager is not None` と同型）。
        self._run_tracer = run_tracer

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

        協働クラス 5 つも本メソッドが組む（run につき 1 組）。いずれも純粋な組み立てで
        あり副作用を持たないため、上の 3 つの順序には影響しない。帳簿（`TradeLedger`）を
        先に組むのは、他の 4 つがそれを協力者として受けるからである。
        """
        config = request.config
        # run のスイッチは 1 度だけ読む（既定は BacktestConfig の宣言が単一ソース）。
        #   呼出側が既に読んでいれば読み直さない（読み取りは run につき 1 回）。
        features = features if features is not None else RunFeatures.of(config)
        bars = list(request.bars)
        # 約定損益の口座通貨丸め桁（既定 None＝丸めず＝byte-identical）。確定トレード生成
        # （`TradeLedger.close`）が本値を TradeRecord に付与し pnl/deal/balance を一致させる。
        profit_round_digits = features.profit_round_digits
        # 市場閉鎖バー（新規成行を約定しない）。既定 None→空集合で既定経路は不変。
        session_gate = self._session_gate(bars)

        # OnInit 前処理
        self._strategy.on_init(config, self._indicators)

        trades: list = []
        deals: list = []
        balance_curve: list[float] = []
        equity_curve: list[float] = []
        spec = request.symbol_spec
        contract_size = spec.contract_size
        # 層2: 含み損益の評価基準を config から引く（既定 "close"＝従来 close 固定で不変）。
        # "bid_ask" 時は売り保有を Ask=close+spread×point で評価するため point_size を渡す。
        floating_pnl_basis = features.floating_pnl_basis
        leverage = request.account.leverage
        stop_out_level = request.account.stop_out_level
        account = Account(
            balance=request.account.initial_deposit,
            contract_size=contract_size,
            floating_pnl_basis=floating_pnl_basis,
            point_size=spec.point_size,
        )

        # ISSUE-308: 決済呼び出しの**不変の文脈**（口座・記録先・銘柄仕様）をここで 1 度だけ束ねる。
        #   これらは 1 回の実行中に変わらないため、各決済地点で書き写す必要がない。
        ledger = TradeLedger(
            account=account,
            contract_size=contract_size,
            leverage=leverage,
            trades=trades,
            deals=deals,
            balance_curve=balance_curve,
            profit_round_digits=profit_round_digits,
        )
        # 証拠金割れの方針は run につき 1 度だけ引く（評価点ごとに引き直さない）。
        stop_out_policy = resolve_stop_out_policy(features.stop_out_action)

        return _RunState(
            bars=bars,
            features=features,
            spec=spec,
            contract_size=contract_size,
            account=account,
            session_gate=session_gate,
            ledger=ledger,
            executor=OrderExecutor(
                account=account,
                ledger=ledger,
                spec=spec,
                leverage=leverage,
                contract_size=contract_size,
                entry_price_basis=features.entry_price_basis,
                pending_oco=features.pending_oco,
            ),
            sltp=SltpMonitor(ledger=ledger, sltp_tie=features.sltp_tie),
            margin_guard=MarginGuard(
                account=account,
                ledger=ledger,
                policy=stop_out_policy,
                stop_out_level=stop_out_level,
                leverage=leverage,
                contract_size=contract_size,
                equity_curve=equity_curve,
                floating_pnl_basis=floating_pnl_basis,
                point_size=spec.point_size,
            ),
            directives=PositionDirectiveApplier(
                position_manager=self._position_manager,
                account=account,
                ledger=ledger,
            ),
            close_trade=ledger.close,
            # 観測口は注入されたものをそのまま持つ（run につき 1 度だけ束ねる）。
            tracer=self._run_tracer,
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

    def execute(self, request: RunBacktestRequest) -> BacktestResult:
        """1 run を実行する（UC-001 の入口）。

        粒度（バーで評価するかティックで評価するか）はスケジュールが引き受ける。入口は
        run のスイッチを 1 度だけ読み、粒度に合うスケジュールを組んで本体へ渡す。
        """
        features = RunFeatures.of(request.config)
        schedule = self._schedule or self._make_schedule(request, features)
        return self._run(request, features, schedule)

    def _make_schedule(
        self, request: RunBacktestRequest, features: RunFeatures
    ) -> Any:
        """run の粒度に合う評価スケジュールを組む（run につき 1 つ）。

        どの run が足の途中の評価点を要するかは選択規則の表が決める（実行経路は条件を
        持たない）。要らない run は 1 バー 1 点で足りる。
        """
        point_size = request.symbol_spec.point_size
        if requires_tick_granularity(features):
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

        本メソッドが持つのは**順序と条件だけ**であり、各段の中身は協働クラスが持つ
        （ISSUE-502 段階 4A）。「いつ・どの条件で呼ぶか」は run の進み方の関心であり、
        「呼ばれたとき何をするか」はそれぞれの規則の所有者の関心である。

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
        executor = state.executor
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
            #   open が割れた保有玉は「バー open クォート」で強制決済される。既定 False で
            #   本ブロックは不活性（ISSUE-022）。規則の中身は `MarginGuard` が持つ。
            if stop_out_at_open and open_trades and not halted:
                open_trades, halted = state.margin_guard.settle_bar_open(
                    open_trades, halted, bar=bar, bar_index=bar_index
                )

            # ★ペンディング経路のサーバ処理。実 MT5 はバー先頭ティック(open)で、EA の
            #   OnTick を呼ぶ**前に**保有玉の SL/TP を処理し、残存ペンディングを評価する。
            #   open で SL/TP が当たった玉は on_new_bar 時点で flat となり、同足で新規
            #   ペンディングを設置できる（2603-01: SL@バー open→同足で次玉設置を再現）。
            #   後段の評価点ループは open の点を再評価するが、生存玉は同クォートで冪等。
            if pending_mode and not halted and has_points and not bar_closed:
                if open_trades:
                    # この時点で当該バーに建てた玉は無いので、建て足の監視抑止は空振りする。
                    open_trades = state.sltp.check(points[0], open_trades, closed=False)
                if executor.has_resting_pending():
                    # 先頭の点（バー open ティック）で 1 回だけトリガ評価する。約定玉は
                    #   序数 0 で積まれ、後段の点は open の判定を抑止する。
                    executor.trigger_resting(open_trades, points[0])

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
            #   完了させる（走査順＝反映順）。建値の導出も約定段が所有する。
            if has_points and market_orders:
                open_trades = executor.fill_market(
                    market_orders, open_trades, bar=bar, bar_index=bar_index
                )

            # ★ペンディングの設置（PROCESS §4.2 拡張）。持続モードでは貼り替えず約定まで
            #   保持する（再アームは足途中の点が担う）。
            if pending_mode and not pending_persistent:
                executor.clear_resting_pending()
            if has_points and pending_orders:
                open_trades = executor.place_pending(open_trades, pending_orders, bar=bar)

            # H → 建玉変更 → ペンディング → I を評価点ごとに行う。「どこで評価するか」は
            #   スケジュールが決め、「評価点で何をするか」は _evaluate_point が持つ。
            for point in points:
                open_trades, halted = self._evaluate_point(
                    state, point, open_trades, halted
                )
                # 実行トレースの観測（唯一の呼出点）。ここである理由: 「その評価点の
                #   全副作用が確定した直後」であり、`account` が当該点のクォートで
                #   値洗いされた後の唯一の瞬間である。`_evaluate_point` の内側へ入れると
                #   早期 return 3 経路ぶんの写しが必要になる（複製）。
                if state.tracer is not None:
                    state.tracer.observe(point, state.account, open_trades, halted)

            prev_close = bar.close

        # ★ペンディング経路: テスト期間終了時に残る建玉を最終足の close クォートで清算する
        #   （清算価格の規則は `OrderExecutor` が持つ）。既定経路は不変。
        if pending_mode and open_trades and bars:
            open_trades = executor.close_all_at_final_bar(open_trades, bars[-1])

        # OnDeinit 集計
        return self._finish_run(
            trades=state.trades,
            deals=state.deals,
            balance_curve=state.balance_curve,
            equity_curve=state.equity_curve,
            initial_deposit=request.account.initial_deposit,
        )

    def _evaluate_point(
        self, state: _RunState, point: Any, open_trades: list, halted: bool
    ) -> "tuple[list, bool]":
        """1 評価点で行うことすべて（両粒度で共有する唯一の手続き）。

        順序は実 MT5 の成立順に従う: サーバが SL/TP を執行し（H）、EA が建玉を触り
        （建玉変更）、最後に口座が再評価される（I）。粒度による違いは点の中身
        （価格の取り方・粒度名・ティック序数）に畳み込まれており、手続き自体は 1 つである。

        本メソッドが持つのは**この成立順だけ**であり、各段の中身は協働クラスが持つ。

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
            state.margin_guard.record_carried_quote(
                eval_bid=point.eval_bid, eval_ask=point.eval_ask
            )
            return open_trades, halted

        closed = state.session_gate.is_closed(point.bar_index)
        open_trades = state.sltp.check(point, open_trades, closed=closed)
        if self._position_manager is not None and open_trades and not closed:
            state.directives.apply_all(
                open_trades,
                bar=point.bar,
                granularity=point.granularity,
                ref_buy=point.pm_ref_buy,
                ref_sell=point.pm_ref_sell,
            )
        # 残存ペンディングのトリガ評価。約定価格＝注文価格（スリッページ 0）。
        #   バー粒度では残存が常に空なので、この段は素通りする。
        if state.executor.has_resting_pending() and not closed:
            state.executor.trigger_resting(open_trades, point)
        # ペンディング持続モードの足途中再アーム（実 MT5 の OnTick 即時再設置に相当）。
        #   SL/TP 決済直後など「保有 0・残存 0」の点で、その点のクォートで即装填する。
        #   バー境界の on_new_bar ではなく決済が起きた実ティックのクォートを使うため、
        #   再アーム価格が実 MT5 と一致する。置いた点自身では約定判定しない（次の点から）。
        if (
            state.features.pending_persistent
            and state.features.pending_lifecycle
            and not halted
            and not open_trades
            and not state.executor.has_resting_pending()
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
                state.executor.rearm(rearm)
        return state.margin_guard.settle(
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
        通らない限り検出できない。集計の定義点を 1 つにして、その食い違いを
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
