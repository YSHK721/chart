"""実行トレースの観測口とティック時刻（ISSUE-508・RUN_TRACE_BASIC_DESIGN §4.4 / §5）。

固定する仕様（段階 1・段階 2 のみ。段階 3 の永続化は本ファイルの対象外）:

    段階 1: エンジンは評価点 1 つの全副作用が確定した直後に観測口へ 1 度だけ差し出す。
        観測口が注入されていない run では観測を 1 回も発行しない（既定経路は無改変）。
    段階 2: 評価点はティックモデルが**供給した時刻をそのまま**運ぶ（加工しない・
        判別しない）。合成疑似ティックが運ぶバー時刻は欠落ではなく真値であり、バー内の
        順序は tick_ordinal が表す。時刻が None になるのはティックを 1 本も持たない点
        （バー粒度の点・ティック 0 件バーの持ち越し点）だけである（設計書 §5.1）。

なぜ「記録行数」を主語にするか（設計書 §4.4 の裁定）:
    窓（どこを残すか）が絞るのは**記録行数**であって `observe` の**呼出回数**ではない。
    tracer を注入した run では呼出は評価点数ぶん起きる（窓判定はその内側で行う）。
    「窓の外は発行 0」を呼出回数の主張として書くと、恒真か実装不能のどちらかになる。

計算量（プロジェクト絶対命令 2026-08-28）:
    測るのは時間ではなく回数。**記録行数 − 窓内の評価点数 = 0** を表明する。
    窓内の評価点数はスケジュールを包む Spy が**独立に**数える——期待値の数字を
    テストへ焼き込むと、浪費（同じ点を 2 度記録する等）が仕様へ昇格する。
    さらにバー数の異なる 2 点で「記録量は窓が決め、run 長が決めない」を固定する。

**恒真にしない**（工程 5 レビュー 🔴-1・🟡-2・🟡-3・設計書 §4.4）:
    どの表明も**記録 0 件で緑になってはならない**。`observe` の呼出を全削除する変異で
    表明 4・表明 3・表明 1 の主 assert がいずれも緑のまま残った＝ISSUE-450 と同型
    （既存 1,233 件が緑のまま 20 日間浪費を保護した）。よって各検定に**正の対照**
    （測っている対象が 0 件でないこと）を必ず同梱する。

**引数の中身を縛る**（工程 5 レビュー 🔴-2）:
    observe が受ける account / open_trades / halted は §6.1 の記録列
    （balance・equity・open_count・open_volume_*・halted）の供給元である。
    中身を縛らないと、段階 3 で open_count が常に 0・halted が常に False で出力
    されても 1 件も検出しない。

**評価点の 3 クラスを網羅する**（工程 5 レビュー 🟡-1）:
    通常の実ティック点・**合成バー点**（ティック 0 件バーの持ち越し点）・**halt 後の点**。
    どれかが fixture に 0 件だと、その点の観測を落とす変異が検出できない。
"""
from __future__ import annotations

import numpy as np
import pytest

from simulator.domain.bar import Bar
from simulator.domain.exceptions import ConfigError
from simulator.usecase.bar_schedule import BarSchedule
from simulator.usecase.evaluation_point import NOT_A_TICK
from simulator.usecase.models import AccountSpec, SymbolSpec
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest
from simulator.usecase.run_trace_ports import RunTracePort
from simulator.usecase.tick_schedule import TickSchedule
from simulator.tests.unit.test_run_backtest_single_engine import (
    _NullIndicators,
    _NullStrategy,
    _OneTickPerBar,
    _OrdersPerBar,
    _bars,
    _config,
    _market,
    _request,
)

_POINT_SIZE = 0.00001


# ---- Spy（観測の記録先・評価点の独立計数） ----

class _RecordingTracer(RunTracePort):
    """観測口の Spy（記録先）。

    段階 3 の TraceWindow（`simulator/adapter/trace/trace_window.py`） はまだ無いので、「どこを残すか」は本 Spy が表現する
    （段階 1 の時点では窓を Spy 側で表現してよい）。

    評価点の同一性は `(bar_index, tick_ordinal)` で表す。`id(point)` は点が解放された
    あとに再利用されうるため、「2 度記録されたか」の判定には使えない。

    `point` の 2 属性だけでなく**受領した 4 引数すべて**の写しを残す。`account` /
    `open_trades` / `halted` は段階 3 の記録列の供給元であり、中身を縛らないと
    「常に 0・常に False」を出力する実装が素通りする（工程 5 レビュー 🔴-2）。
    引数は読むだけで書き換えない（Port の事後条件 1＝非侵襲）。
    """

    def __init__(self, window=None):
        self._window = window
        self.calls = 0
        self.rows: "list[tuple]" = []
        #: 観測時点の口座 equity（値洗い済みか＝呼出位置の表明に使う）。
        self.equity_at_observe: "list[float]" = []
        #: 観測時点の保有玉数（open_count 列の供給元）。
        self.open_counts: "list[int]" = []
        #: 観測時点の保有量（買い/売り別。`open_volume_*` 列の供給元）。
        self.open_volumes: "list[tuple]" = []
        #: 観測時点の halt 状態（`halted` 列の供給元）。
        self.halted_flags: "list[bool]" = []
        #: 渡された口座オブジェクトの同一性（写しを渡す実装を赤にするため・§7.2 条件 9）。
        self.account_ids: "list[int]" = []

    def observe(self, point, account, open_trades, halted):
        self.calls += 1
        if self._window is not None and not self._window(point):
            return
        self.rows.append((point.bar_index, point.tick_ordinal))
        self.equity_at_observe.append(account.equity)
        self.open_counts.append(len(open_trades))
        self.open_volumes.append(
            (
                sum(t.position.volume for t in open_trades if t.position.side == "buy"),
                sum(t.position.volume for t in open_trades if t.position.side == "sell"),
            )
        )
        self.halted_flags.append(bool(halted))
        self.account_ids.append(id(account))


class _CountingSchedule:
    """スケジュールを包み、生んだ評価点を**独立に**数える Spy。

    記録行数の期待値をテストへ焼き込まないために置く（数えるのは装置であって人ではない）。
    包むだけで点そのものは素通しする（値を変えない）。
    """

    def __init__(self, inner, window=None):
        self._inner = inner
        self._window = window
        self.produced: "list[tuple]" = []
        self.in_window: "list[tuple]" = []
        #: 合成バー点（ティック 0 件バーの持ち越し点）。fixture 網羅の正の対照に使う。
        self.synthetic: "list[tuple]" = []

    @property
    def id(self):
        return self._inner.id

    def points(self, bar_index, bar, prev_close):
        for point in self._inner.points(bar_index, bar, prev_close):
            key = (point.bar_index, point.tick_ordinal)
            self.produced.append(key)
            if point.is_synthetic_bar_point:
                self.synthetic.append(key)
            if self._window is None or self._window(point):
                self.in_window.append(key)
            yield point


def _window_bars_2_to_5(point):
    """窓の一例（半開 [2, 5) のバー範囲）。段階 3 の TraceWindow の代役。"""
    return 2 <= point.bar_index < 5


# ---- fixture: 評価点の 3 クラスを作り分ける ----

class _TicksExceptOnEmptyBars:
    """指定した順番のバーだけティック 0 件（TickModelPort の事後条件＝空列）を返す。

    `ticks_of` は**バーにつき 1 回**呼ばれる（`TickSchedule.points` の事後条件）ので、
    呼出の順番がバーの順番と一致する。
    """

    def __init__(self, empty_bars=()):
        self._empty = set(empty_bars)
        self._seen = 0

    def ticks_of(self, bar, prev_close):
        index = self._seen
        self._seen += 1
        if index in self._empty:
            return []
        return [(bar.close, bar.low, bar.high, bar.time)]


class _BuyOnce(_NullStrategy):
    """バー 0 で 1 lot 買う（証拠金割れを起こすため）。"""

    def on_new_bar(self, bar_index, indicators, account):
        return [_market("buy")] if bar_index == 0 else []


def _halting_bars():
    """バー 1 で急落し証拠金を割る足（以降は halt 後の評価点になる）。"""
    prices = [1.10, 1.00, 1.00, 1.00]
    return [
        Bar(
            time=np.datetime64("2024-01-01T00:00") + np.timedelta64(i, "m"),
            open=p, high=p, low=p, close=p, volume=1.0, spread=0,
        )
        for i, p in enumerate(prices)
    ]


def _halting_request():
    """証拠金割れで halt する run（`halted=True` の評価点を作る）。

    contract=100,000・leverage=100 → 必要証拠金 1,100。deposit 10,000・stop_out 50%。
    バー 1 で 1.10→1.00 の含み損 10,000 が出て equity=0＝割れる。
    `close_and_halt` は送出せず強制決済して halt するので、以降の点が halt 後の点になる。
    """
    return RunBacktestRequest(
        config=_config(stop_out_action="close_and_halt"),
        bars=_halting_bars(),
        symbol_spec=SymbolSpec(
            contract_size=100_000.0, volume_min=0.01, volume_max=100.0,
            volume_step=0.01, stops_level=0, digits=5, point_size=_POINT_SIZE,
        ),
        account=AccountSpec(
            initial_deposit=10_000.0, leverage=100.0, stop_out_level=50.0
        ),
    )


def _run(
    *,
    bar_count=8,
    window=None,
    strategy=None,
    observe=True,
    tracer=None,
    tick_model=None,
    request=None,
):
    """1 run 実行し `(result, tracer, schedule_spy)` を返す。

    窓の述語は**本関数だけが配る**。記録先の Spy と計数の Spy へ別々に渡す形にすると、
    2 つが食い違ったまま「記録行数 − 窓内の評価点数 = 0」が緑になりうる（測っている
    ものが消える）。1 つの述語が必ず両方へ届くことを構造で保証する。

    `tracer` を渡すとその実体を使う（同じ計数器で 2 つの run を続けて測るため）。

    スケジュールは run につき 1 つ組む（注入スケジュールは状態を持つため使い回さない）。
    """
    if tracer is None and observe:
        tracer = _RecordingTracer(window=window)
    model = tick_model if tick_model is not None else _OneTickPerBar()
    inner = TickSchedule(
        tick_model=model, pending_lifecycle=False, point_size=_POINT_SIZE
    )
    schedule = _CountingSchedule(inner, window=window)
    interactor = RunBacktestInteractor(
        strategy=strategy if strategy is not None else _NullStrategy(),
        indicators=_NullIndicators(),
        tick_model=model,
        schedule=schedule,
        run_tracer=tracer,
    )
    result = interactor.execute(
        request
        if request is not None
        else _request(_bars(bar_count), config=_config())
    )
    return result, tracer, schedule


def _orders_strategy():
    return _OrdersPerBar(
        {0: [_market("buy")], 2: [_market("sell")], 4: [_market("buy")]}
    )


#: 評価点の 3 クラスを網羅する fixture（工程 5 レビュー 🟡-1・設計書 §4.4）。
#:   どれかが 0 件だと、その点の観測を落とす変異が検出できない。
def _fixture_plain():
    return {"strategy": _orders_strategy(), "bar_count": 8}


def _fixture_with_empty_tick_bars():
    return {
        "strategy": _orders_strategy(),
        "bar_count": 8,
        "tick_model": _TicksExceptOnEmptyBars(empty_bars=(3, 6)),
    }


def _fixture_halting():
    return {"strategy": _BuyOnce(), "request": _halting_request()}


def _fixture_many_ticks_per_bar():
    """1 バー複数ティック（段階 3 §7.2 通過条件 8）。

    全 fixture が `points_per_bar=1`・`tick_ordinals ⊂ {0, -1}` のままだと、
    `points.parquet` の主キー `(bar_index, tick_ordinal)` の一意性が `bar_index` だけで
    成立してしまい、`tick_ordinal` を取り落とす実装が素通りする。材料は同ファイル内の
    `_NTicksPerBar`（段階 2 の計算量検定が既に使っている）。
    """
    return {
        "strategy": _orders_strategy(),
        "bar_count": 8,
        "tick_model": _NTicksPerBar(4),
    }


_FIXTURES = [
    ("plain", _fixture_plain),
    ("empty_tick_bars", _fixture_with_empty_tick_bars),
    ("halting", _fixture_halting),
    ("many_ticks_per_bar", _fixture_many_ticks_per_bar),
]


# ---- §4.4 表明 1: 未注入なら観測の発行 0 ----

class TestNoObservationIsIssuedWhenNoTracerIsInjected:
    """観測口を注入しない run が観測を 1 回も発行しないこと（既定経路は無改変）。"""

    def test_the_counter_moves_when_injected_and_never_moves_when_not(self):
        """**同じ計数器**で「注入 run では増える → 未注入 run では 1 も増えない」。

        是正前は「どこへも渡していない Spy の calls が 0」を測っており、構造的に恒真
        だった（渡していないのだから呼ばれ得ない）。同一の計数器が実際に動くことを先に
        実証してから、未注入 run で 1 も増えないことを測る（工程 5 レビュー 🟡-2）。
        """
        # Arrange: 1 つの計数器を 2 つの run で使い回す。
        tracer = _RecordingTracer()

        # Act 1: 注入した run（計数器が動くことの実証）。
        _r1, _t1, injected_schedule = _run(tracer=tracer, **_fixture_plain())
        moved = tracer.calls

        # Act 2: 同じ計数器を**注入しない** run。
        _r2, none_tracer, plain_schedule = _run(observe=False, **_fixture_plain())

        # Assert: 正の対照（計数器は動く・両 run とも評価点を生んでいる）。
        assert moved > 0, "計数器が一度も動いていない（検定が何も測っていない）"
        assert injected_schedule.produced and plain_schedule.produced
        # 未注入 run は観測を 1 も発行しない。
        assert none_tracer is None
        assert tracer.calls - moved == 0

    def test_the_run_state_carries_no_tracer_by_default(self):
        """既定（未注入）では開始状態が観測口を持たないこと。"""
        # Arrange / Act
        interactor = RunBacktestInteractor(
            strategy=_NullStrategy(),
            indicators=_NullIndicators(),
            tick_model=_OneTickPerBar(),
        )
        state = interactor._begin_run(_request(_bars(4), config=_config()))

        # Assert
        assert state.tracer is None


# ---- §4.4 表明 2・3: 記録行数 − 窓内の評価点数 = 0 / 1 点 1 回 ----

class TestEveryRecordedRowIsAnEvaluationPointInsideTheWindow:
    """記録が「窓内の評価点」と 1 対 1 であること（作って捨てる形・二重記録の不在）。

    評価点の 3 クラス（通常点・合成バー点・halt 後の点）すべてで測る。1 クラスでも
    fixture に現れないと、そのクラスの観測を落とす変異が素通りする。
    """

    @pytest.mark.parametrize("name,fixture", _FIXTURES, ids=[n for n, _ in _FIXTURES])
    def test_the_recorded_rows_minus_the_points_produced_is_zero(self, name, fixture):
        # Arrange / Act: 窓なし＝生んだ点はすべて記録されるべき。
        _result, tracer, schedule = _run(**fixture())

        # Assert: 発行（記録行）− 使用（生んだ評価点）= 0。
        assert len(tracer.rows) - len(schedule.produced) == 0, (
            len(tracer.rows),
            len(schedule.produced),
        )
        assert tracer.rows == schedule.produced
        # 正の対照: 0 件なら差 0 は恒真になる。
        assert schedule.produced, "評価点が 0 件（検定が何も測っていない）"

    def test_the_fixtures_cover_the_three_classes_of_evaluation_point(self):
        """3 クラスが実際に fixture に現れること（網羅の正の対照）。

        本検定が無いと、fixture から合成バー点・halt 後の点が消えても上の検定は
        緑のままになり、そのクラスの観測を落とす変異が検出できなくなる。
        """
        # Arrange / Act
        _r0, plain_tracer, plain_schedule = _run(**_fixture_plain())
        _r1, _t1, empty_schedule = _run(**_fixture_with_empty_tick_bars())
        _r2, halting_tracer, _s2 = _run(**_fixture_halting())

        # Assert: 通常点・保有のある点・合成バー点・halt 後の点がそれぞれ 1 件以上。
        assert plain_schedule.produced
        assert plain_schedule.synthetic == [], "通常 fixture に合成点が混ざっている"
        assert any(n > 0 for n in plain_tracer.open_counts), "保有のある点が 0 件"
        assert empty_schedule.synthetic, "合成バー点が 0 件（ティック 0 件バーが無い）"
        assert any(halting_tracer.halted_flags), "halt 後の点が 0 件"

    @pytest.mark.parametrize("name,fixture", _FIXTURES, ids=[n for n, _ in _FIXTURES])
    def test_no_evaluation_point_is_recorded_twice(self, name, fixture):
        # Arrange / Act
        _result, tracer, _schedule = _run(**fixture())

        # Assert: 同じ評価点が 2 行以上に現れない。
        assert len(tracer.rows) == len(set(tracer.rows)), tracer.rows
        # 正の対照: 記録 0 件なら重複なしは恒真になる（工程 5 レビュー 🟡-3）。
        assert tracer.rows, "記録が 0 件（1 点 1 回の表明が恒真になっている）"

    def test_the_window_keeps_only_the_points_inside_it(self):
        # Arrange / Act
        _result, tracer, schedule = _run(
            window=_window_bars_2_to_5, **_fixture_plain()
        )

        # Assert: 窓内の評価点と記録が一致する。
        assert len(tracer.rows) - len(schedule.in_window) == 0
        assert set(tracer.rows) == set(schedule.in_window)
        # 窓の外は 1 行も記録されない。
        outside = set(schedule.produced) - set(schedule.in_window)
        assert outside, "窓の外の評価点が 0 件（窓が効いていない fixture）"
        assert set(tracer.rows) & outside == set()
        # 正の対照: 窓内が空なら上のすべてが恒真になる。
        assert schedule.in_window, "窓内の評価点が 0 件（検定が何も測っていない）"


# ---- §4.4 表明 4: オーダー（記録量は窓が決め、run 長が決めない） ----

class TestTheRecordedVolumeIsDecidedByTheWindowNotTheRunLength:
    """バー数の異なる 2 点で記録量が変わらないこと。"""

    def test_the_recorded_rows_do_not_grow_with_the_number_of_bars(self):
        # Arrange / Act: 同じ窓のまま run 長だけを 4 倍にする。
        measured = {}
        for bar_count in (12, 48):
            _result, tracer, schedule = _run(
                bar_count=bar_count,
                window=_window_bars_2_to_5,
                strategy=_orders_strategy(),
            )
            measured[bar_count] = (len(tracer.rows), set(tracer.rows))
            # 各点で「窓の外は 1 行も記録されない」を保つ。
            outside = set(schedule.produced) - set(schedule.in_window)
            assert set(tracer.rows) & outside == set(), bar_count

        # 正の対照: 両方 0 件なら差 0 は恒真になる（工程 5 レビュー 🔴-1）。
        assert measured[12][0] > 0, "記録が 0 件（オーダーの表明が恒真になっている）"
        # Assert: 入力を 4 倍にしても記録量は増えない（記録量は窓が決める）。
        assert measured[48][0] - measured[12][0] == 0, {
            k: v[0] for k, v in measured.items()
        }
        assert measured[48][1] == measured[12][1]


# ---- §4.4 表明 5: 非侵襲（観測が run の結果を変えない） ----

class TestTheObservationDoesNotChangeTheResult:
    """観測あり／なしで run の結果（trades / deals / 両曲線 / stats）が一致すること。"""

    def test_the_result_is_identical_with_and_without_observation(self):
        # Arrange / Act: 同じ入力を観測なし／ありで 1 run ずつ走らせる。
        plain, _none, _s1 = _run(observe=False, **_fixture_plain())
        observed, tracer, _s2 = _run(observe=True, **_fixture_plain())

        # Assert: 結果の 5 項目が一致する。
        assert observed.trades == plain.trades
        assert observed.deals == plain.deals
        assert observed.equity_curve == plain.equity_curve
        assert observed.balance_curve == plain.balance_curve
        assert observed.stats == plain.stats
        # 正の対照: 決済が 1 件も起きない run／観測が起きていない run では何も測れていない。
        assert len(plain.trades) > 0
        assert tracer.calls > 0


# ---- 🔴-2: observe が受ける 4 引数の中身が拘束されていること ----

class TestTheObservationCarriesTheStateOfThatPoint:
    """`account` / `open_trades` / `halted` の中身が縛られていること。

    これらは段階 3 の記録列（balance・equity・open_count・open_volume_*・halted）の
    **供給元そのもの**である。縛らないと「open_count が常に 0」「halted が常に False」
    を出力する実装が 1 件も検出されない（工程 5 レビュー 🔴-2）。
    """

    def test_the_open_trades_of_the_point_are_handed_over(self):
        """保有玉が在る点では、その保有列が観測へ渡ること。"""
        # Arrange / Act
        _result, tracer, schedule = _run(**_fixture_plain())

        # Assert: 保有のある点が 1 件以上ある（空列を渡す実装なら 0 件になる）。
        assert any(n > 0 for n in tracer.open_counts), tracer.open_counts
        # 正の対照: 観測そのものが 0 件なら上は恒真になる。
        assert tracer.open_counts
        assert len(tracer.open_counts) == len(schedule.produced)

    def test_the_halt_state_of_the_point_is_handed_over(self):
        """halt した run では、halt 後の点が `halted=True` として観測へ渡ること。"""
        # Arrange / Act
        result, tracer, _schedule = _run(**_fixture_halting())

        # Assert: 正の対照（run が実際に halt している）。
        assert [t.exit_reason for t in result.trades] == ["stop_out"]
        # halt 後の点が観測に現れる（常に False を渡す実装なら 0 件になる）。
        assert any(tracer.halted_flags), tracer.halted_flags
        # halt は一度立てば戻らない（False→True の単調な並びである）。
        assert tracer.halted_flags == sorted(tracer.halted_flags)

    def test_the_account_handed_over_is_the_live_account_of_the_run(self):
        """観測へ渡る口座が run の生きた実体であること（写しでも初期値でもない）。"""
        # Arrange / Act
        result, tracer, _schedule = _run(**_fixture_plain())

        # Assert: 観測時の equity が run の equity 系列と一致する。
        assert tracer.equity_at_observe == result.equity_curve
        # 正の対照: equity が動かない run では何も測れていない。
        assert len(set(tracer.equity_at_observe)) > 1, tracer.equity_at_observe


# ---- 🔴-3: 呼出位置（§4.2 の唯一の設計根拠）が機械的に固定されていること ----

class TestTheObservationHappensAfterThePointHasSettled:
    """観測時点で、当該点の副作用が**既に**確定していること。

    Port の事前条件（`run_trace_ports.py`）は「`account` が当該点のクォートで値洗い
    されていること＝呼出点は評価点ループの直後ただ 1 箇所」と宣言している。宣言だけでは
    呼出を評価前へ動かす変異が緑のまま通るため、構造で固定する（工程 5 レビュー 🔴-3）。

    測り方: MarginGuard は評価点 1 つにつき equity をちょうど 1 点積む。観測が評価の
    **後**なら、n 番目の観測で見える equity は積まれた n 番目の値と一致する。観測が
    評価の**前**なら 1 つずれる（初期値から始まり最後の点が観測されない）。
    """

    def test_the_equity_seen_at_each_observation_is_the_one_just_recorded(self):
        # Arrange / Act: 保有玉があり equity が点ごとに動く run。
        result, tracer, schedule = _run(**_fixture_plain())

        # Assert: 観測列と equity 系列が値・順序ともに一致する。
        assert tracer.equity_at_observe == result.equity_curve
        # 正の対照: 点ごとに equity が動いていないと、ずれても一致してしまう。
        assert len(set(tracer.equity_at_observe)) > 1, tracer.equity_at_observe
        assert len(tracer.rows) == len(schedule.produced)

    def test_the_first_observation_is_not_the_untouched_opening_balance(self):
        """最初の観測が「まだ何も評価していない口座」でないこと。

        呼出を評価点の前へ動かすと、最初の観測は建玉直後・値洗い前の口座（＝初期
        残高そのもの）になる。その形を名指しで赤にする。
        """
        # Arrange / Act
        result, tracer, _schedule = _run(**_fixture_plain())

        # Assert
        assert tracer.equity_at_observe[0] == result.equity_curve[0]
        # 正の対照: 建玉により初回 equity は初期残高から動いている。
        assert tracer.equity_at_observe[0] != 10_000.0


# ---- 段階 2: 評価点が実ティックの時刻を運ぶ ----

def _real_tick_frame(timestamps):
    """canonical 実ティック frame（timestamp/bid/ask/last/volume）。"""
    import pandas as pd

    return pd.DataFrame(
        {
            "timestamp": list(timestamps),
            "bid": [1.10 + i * 0.01 for i in range(len(timestamps))],
            "ask": [1.12 + i * 0.01 for i in range(len(timestamps))],
            "last": [1.11 + i * 0.01 for i in range(len(timestamps))],
            "volume": [1.0] * len(timestamps),
        }
    )


def _bar_at(minute=0):
    t = np.datetime64("2024-01-01T00:00:00") + np.timedelta64(minute, "m")
    return Bar(time=t, open=1.1, high=1.3, low=1.0, close=1.2, volume=10.0, spread=0)


def _tick_points(tick_model, bar, *, pending_lifecycle=False):
    schedule = TickSchedule(
        tick_model=tick_model,
        pending_lifecycle=pending_lifecycle,
        point_size=_POINT_SIZE,
    )
    return list(schedule.points(0, bar, prev_close=1.0))


class TestTheArgumentsAreBoundOnBothSides(object):
    """段階 3 §7.2 通過条件 6・7・9 の穴を塞ぐ追加表明。

    既存の表明は「1 件以上あること」（`any(...)`）で測っていたため、次の変異が
    緑のまま通っていた（設計書 §7.2 の現状欄・実測）:

        条件 6: `halted` を**常に True** で渡す（`any(halted_flags)` は緑のまま）。
        条件 7: open_count に `+1` バイアスを入れる（`any(n>0)` は緑のまま）。
        条件 9: `account` の**写し**を渡す（equity 系列の一致だけでは区別できない）。
    """

    def test_a_run_that_never_halts_reports_false_at_every_point(self):
        """条件 6 の片側: 「常に True」を赤にする。"""
        # Arrange / Act
        _result, tracer, _schedule = _run(**_fixture_plain())

        # Assert
        assert set(tracer.halted_flags) == {False}, tracer.halted_flags
        # 正の対照: 観測 0 件なら上は恒真になる。
        assert tracer.halted_flags, "観測が 0 件（検定が何も測っていない）"

    def test_a_halting_run_reports_both_sides(self):
        """条件 6 のもう片側: 「常に False」を赤にする。"""
        # Arrange / Act
        _result, tracer, _schedule = _run(**_fixture_halting())

        # Assert
        assert set(tracer.halted_flags) == {False, True}, tracer.halted_flags

    def test_the_open_count_equals_the_volume_carried_by_the_same_list(self):
        """条件 7: 存在検査ではなく**値**で縛る。

        本 fixture の発注はすべて 1.0 lot・同方向なので、件数と量の総和は常に一致する。
        `+1` バイアスも、件数を定数で返す実装も赤になる。
        """
        # Arrange / Act: 同方向の成行を 3 本積む（反対売買は reverse 決済になり
        #   保有数が常に 1 のままで、定数を返す実装と区別できない）。
        _result, tracer, _schedule = _run(
            strategy=_OrdersPerBar(
                {0: [_market("buy")], 2: [_market("buy")], 4: [_market("buy")]}
            ),
            bar_count=8,
        )

        # Assert
        for n, (buy, sell) in zip(tracer.open_counts, tracer.open_volumes):
            assert buy + sell == pytest.approx(n * 1.0), (n, buy, sell)
        # 正の対照: 保有数が動かない run では上は恒真になる。
        assert len(set(tracer.open_counts)) > 1, tracer.open_counts

    def test_the_account_handed_over_is_the_same_object_every_time(self):
        """条件 9: 「写しでないこと」を主張する検定が写しを実際に検出すること。

        既存の `test_the_account_handed_over_is_the_live_account_of_the_run` は
        equity 系列との一致だけを見ており、**各点で口座を写して渡す実装**を検出できない
        （写しでも当該点の equity は一致する）。検定名の主張と検出範囲を一致させるため、
        同一性そのものを測る表明をここへ足す。
        """
        # Arrange / Act
        _result, tracer, schedule = _run(**_fixture_plain())

        # Assert: 全観測が run の同一の口座実体を受け取っている。
        assert len(set(tracer.account_ids)) == 1, len(set(tracer.account_ids))
        # 正の対照: 観測が 1 件以下なら上は恒真になる。
        assert len(tracer.account_ids) > 1, tracer.account_ids
        assert len(tracer.account_ids) == len(schedule.produced)


class TestTheFixturesCoverIntrabarOrdinals:
    """§7.2 通過条件 8 の正の対照（`tick_ordinal > 0` の点が実在すること）。"""

    def test_the_many_ticks_fixture_produces_more_than_one_point_per_bar(self):
        # Arrange / Act
        _result, tracer, _schedule = _run(**_fixture_many_ticks_per_bar())

        # Assert
        ordinals = {ordinal for _bar, ordinal in tracer.rows}
        assert {0, 1, 2, 3} <= ordinals, ordinals
        # 同一バーに複数点が在る（主キーが bar_index だけでは一意にならない形）。
        from collections import Counter

        per_bar = Counter(bar for bar, _o in tracer.rows)
        assert max(per_bar.values()) >= 4, per_bar

    def test_the_other_fixtures_alone_would_not_exercise_the_ordinal(self):
        """正の対照の対: 旧 fixture 群だけでは序数が {0, -1} に留まること。

        これが無いと、新 fixture を外しても上の検定が消えるだけで、
        「序数を取り落とす実装が素通りする」状態へ静かに戻る。
        """
        # Arrange / Act
        seen = set()
        for _name, fixture in _FIXTURES[:3]:
            _result, tracer, _schedule = _run(**fixture())
            seen |= {ordinal for _bar, ordinal in tracer.rows}

        # Assert
        assert seen <= {0, NOT_A_TICK}, seen


class TestTheEvaluationPointCarriesTheRealTickTime:
    """実ティック経路の点が、そのティック固有の実時刻を運ぶこと。"""

    def test_each_real_tick_point_carries_its_own_distinct_time(self):
        """実ティック経路（`RealTickModel`）で時刻がティックごとに異なること。

        バー時刻の使い回し（合成疑似ティックの形）でないことを固定する。
        """
        # Arrange: バー [00:00, 01:00) の内側に 3 本の実ティック。
        from simulator.adapter.execution.tick_model import RealTickModel

        stamps = [
            np.datetime64("2024-01-01T00:00:10"),
            np.datetime64("2024-01-01T00:00:30"),
            np.datetime64("2024-01-01T00:00:45"),
        ]
        bar = _bar_at(0)

        # Act
        points = _tick_points(RealTickModel(_real_tick_frame(stamps)), bar)

        # Assert: 各点が自分のティックの実時刻を運ぶ（バー時刻の使い回しではない）。
        assert [p.tick_time for p in points] == stamps
        assert len({p.tick_time for p in points}) == len(stamps)
        assert all(p.tick_time != bar.time for p in points)

    def test_a_real_tick_at_the_bar_time_keeps_its_own_time(self):
        """バー時刻ちょうどに来た実ティックも、その実時刻のまま落ちないこと（§5.1）。

        供給された時刻をそのまま運ぶ規則には端の場合が無い。値比較の判別子
        （`tick_time != bar.time` なら実ティック）を置くと、この 1 点だけが `None` へ
        落ちる——実ティックの時刻が失われる形であり、判別材料が無いのに判別を要求した
        初版規則の帰結である。本検定はその形の再導入を赤にする。
        """
        # Arrange: バー先頭ちょうど＋内側 1 本。
        from simulator.adapter.execution.tick_model import RealTickModel

        stamps = [
            np.datetime64("2024-01-01T00:00:00"),
            np.datetime64("2024-01-01T00:00:20"),
        ]
        bar = _bar_at(0)

        # Act
        points = _tick_points(RealTickModel(_real_tick_frame(stamps)), bar)

        # Assert: どちらの点も供給された実時刻をそのまま運ぶ（落ちない）。
        assert points[0].tick_time is not None
        assert points[0].tick_time == stamps[0]
        assert points[1].tick_time == stamps[1]


class TestTheScheduleDoesNotReinterpretTheSuppliedTime:
    """供給された時刻をそのまま運ぶこと（加工・判別をしない・設計書 §5.1）。"""

    def test_the_synthetic_tick_path_carries_the_bar_time(self):
        """合成ティック経路（4 疑似ティック）は `bar.time` をそのまま運ぶこと。

        `OhlcExpandTickModel` の 4 疑似ティックが `bar.time` を持つのは**欠落ではなく
        事実**である（実 MT5 の OHLC 疑似ティックは分未満の時刻を持たない。
        `adapter/execution/tick_model.py:42` が `bar.time` を返すのはそれが真値だから）。
        バー内の順序は `tick_ordinal` が既に表しているので、`None` へ落とす理由が無い。
        """
        # Arrange
        from simulator.adapter.execution.tick_model import OhlcExpandTickModel

        bar = _bar_at(0)

        # Act
        points = _tick_points(OhlcExpandTickModel(), bar)

        # Assert: 4 点とも供給された真値（バー時刻）を運ぶ。
        assert len(points) == 4
        assert [p.tick_time for p in points] == [bar.time] * 4
        # バー内の順序は序数が担う（時刻ではない）。
        assert [p.tick_ordinal for p in points] == [0, 1, 2, 3]

    def test_the_bar_schedule_carries_no_tick_time(self):
        # Arrange / Act
        schedule = BarSchedule(floating_pnl_basis="close", point_size=_POINT_SIZE)
        points = list(schedule.points(0, _bar_at(0), prev_close=1.0))

        # Assert
        assert len(points) == 1
        assert points[0].tick_time is None

    def test_a_zero_tick_bar_point_carries_no_tick_time(self):
        """ティック 0 件バーの持ち越し点も固有の時刻を持たないこと。"""
        # Arrange: 当該バー区間に実ティックが 1 本も無い frame。
        from simulator.adapter.execution.tick_model import RealTickModel

        stamps = [np.datetime64("2024-01-01T00:05:00")]

        # Act
        points = _tick_points(RealTickModel(_real_tick_frame(stamps)), _bar_at(0))

        # Assert
        assert len(points) == 1
        assert points[0].is_synthetic_bar_point is True
        assert points[0].tick_ordinal == NOT_A_TICK
        assert points[0].tick_time is None


# ---- 段階 2 の計算量: 点の発行 − ティック本数 = 0 ----

class _NTicksPerBar:
    """バーにつき `n` 本の実ティック（時刻は 1 秒刻みで相異なる）を供給する。"""

    def __init__(self, n):
        self._n = n

    def ticks_of(self, bar, prev_close):
        for i in range(self._n):
            yield (
                bar.close,
                bar.low,
                bar.high,
                bar.time + np.timedelta64(i + 1, "s"),
            )


class TestTheTickScheduleIssuesOnePointPerTick:
    """ティック時刻を載せても「作ってから捨てる」形が生まれないこと。"""

    @pytest.mark.parametrize("tick_count", [3, 12])
    def test_the_points_issued_minus_the_ticks_supplied_is_zero(self, tick_count):
        # Arrange / Act
        points = _tick_points(_NTicksPerBar(tick_count), _bar_at(0))

        # Assert: 発行（点）− 使用（供給されたティック）= 0。
        assert len(points) - tick_count == 0

    def test_the_point_count_tracks_the_tick_count_at_two_sizes(self):
        """ティック本数の異なる 2 点で、点の発行が本数だけで決まること。"""
        # Arrange / Act
        measured = {
            n: len(_tick_points(_NTicksPerBar(n), _bar_at(0))) for n in (3, 12)
        }

        # Assert
        assert measured[12] - measured[3] == 12 - 3, measured

    def test_every_issued_point_carries_a_distinct_tick_time(self):
        """供給されたティック本数ぶんの相異なる時刻が点に載ること。"""
        # Arrange / Act
        points = _tick_points(_NTicksPerBar(12), _bar_at(0))

        # Assert
        assert len({p.tick_time for p in points}) == 12


# ---- §7.0: 観測の例外はエンジンが握らない（工程 5 レビュー 🟡-A） ----

class _RaisingTracer(RunTracePort):
    """観測で契約違反の例外を送出する具象（§7.0 の「呼出側の契約違反」を模す）。

    実装の義務は「**運用上の失敗**で送出しないこと」であり、受理集合外の時刻など
    呼出側の契約違反は送出してよい／すべきである（設計書 §7.0 の射程限定）。
    エンジンが握ると、時刻の壊れたトレースが静かに出る。
    """

    def __init__(self):
        self.calls = 0

    def observe(self, point, account, open_trades, halted):
        self.calls += 1
        raise ConfigError(
            "観測が受理できない時刻表現を受け取った（検定用の契約違反）",
            context={"bar_index": point.bar_index},
        )


class TestTheEngineDoesNotSwallowAnObservationFailure:
    """エンジンが `observe` の例外を握らないこと（§7.0 の裁定）。

    宣言だけでは、呼出点を `try` / `except Exception: pass` で包む変異が緑のまま通る
    （実測: trace 関連・段階 1/2 の 209 件で緑）。握れば「記録が欠けても誰も気づけない」
    ——設計書が却下した案そのものになる。構造ではなく**振る舞い**で固定する。
    """

    def test_the_exception_reaches_the_caller(self):
        # Arrange
        tracer = _RaisingTracer()

        # Act / Assert: run の呼出側まで伝播する。
        with pytest.raises(ConfigError):
            _run(tracer=tracer, **_fixture_plain())

        # 正の対照: 観測が 1 度も起きていなければ、この表明は何も測っていない。
        assert tracer.calls > 0, "観測が発行されていない（検定が空振り）"

    def test_a_healthy_tracer_still_completes_the_run(self):
        """対の正の対照: 例外を出さない具象なら run は完走する。

        これが無いと「そもそも常に落ちる構成」でも上の表明が緑になる。
        """
        # Arrange / Act
        result, tracer, schedule = _run(**_fixture_plain())

        # Assert
        assert result is not None
        assert tracer.calls == len(schedule.produced) > 0
