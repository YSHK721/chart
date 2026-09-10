"""ColumnarRunTrace: 何を残すか（点粒度）（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.1/§6.5.0）。

固定する仕様:

    1. 列は §6.1 の 18 列ちょうど（1 評価点 1 行）。列の宣言（`COLUMNS`）は本モジュールが
       公開し、`simulator/sim_ui/adapter/trace_writer.py` はそれを**読むだけ**にする（意味を書き写せば 2 箇所化する・D-3b）。
    2. `time` 列の出所は `point.tick_time`、それが `None` のときは `point.bar.time`
       （§6.5.0）。値からの推定ではなく、契約上宣言された不在（ティックを持たない点）への
       充当である。
    3. **導出点は本クラスただ 1 つ**であり、得た epoch 値を `time` 列にも
       `window.contains(epoch)` にも渡す。2 箇所で導出すると「窓が通した点の `time` 列が
       窓の外」という食い違いが**例外を出さずに**起こる。
    4. 型の正規化は `domain.bar_time.epoch_seconds` が単一ソース（§7.2 通過条件 5）。
       `time` 列は epoch 秒の **int** である。
    5. 記録はエンジンの状態を変えない（`RunTracePort` の事後条件 1）。
    6. pandas / pyarrow を import しない（列は素の `list`・D-5）。

計算量（プロジェクト絶対命令 2026-08-28・設計書 §4.4）:
    測るのは時間ではなく回数。**記録行数 − 窓内の評価点数 = 0** を表明する。窓内の
    評価点数はスケジュールを包む Spy が**独立に**数える（数字をテストへ焼き込まない）。
    さらにバー数の異なる 2 点で「記録量は窓が決め、run 長が決めない」を固定する。

**恒真にしない**（設計書 §4.4）: どの表明も**記録 0 件で緑になってはならない**。
    各検定に正の対照（測っている対象が 0 件でないこと）を必ず同梱する。

**値を縛る**（§7.2 通過条件 6・7）: `halted` は**両側**（常に True も赤）、open_count は
    存在検査（`any(n>0)`）ではなく**値**で縛る（`+1` バイアス変異を赤にする）。
"""
from __future__ import annotations

import ast
import pathlib

import numpy as np
import pytest

from simulator.adapter.trace.columnar_run_trace import COLUMNS, ColumnarRunTrace
from simulator.adapter.trace.trace_window import TraceWindow
from simulator.domain.bar import Bar
from simulator.domain.exceptions import ConfigError
from simulator.usecase.evaluation_point import (
    BAR_GRANULARITY,
    NOT_A_TICK,
    TICK_GRANULARITY,
    EvaluationPoint,
)
from simulator.usecase.run_trace_ports import RunTracePort
from simulator.tests.unit.test_run_trace_observation import (
    _CountingSchedule,
    _NTicksPerBar,
    _TicksExceptOnEmptyBars,
    _fixture_halting,
    _fixture_plain,
    _fixture_with_empty_tick_bars,
    _orders_strategy,
)
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
from simulator.usecase.run_backtest import RunBacktestInteractor
from simulator.usecase.tick_schedule import TickSchedule

_POINT_SIZE = 0.00001
_EPOCH = 1_704_067_200  # 2024-01-01T00:00:00Z


# ---- 手組みの入力（列の意味を値で縛る） ----

def _bar(minute=0, close=1.2):
    return Bar(
        time=np.datetime64("2024-01-01T00:00:00") + np.timedelta64(minute, "m"),
        open=1.1, high=1.3, low=1.0, close=close, volume=10.0, spread=0,
    )


def _point(*, bar=None, tick_time=None, bar_index=0, tick_ordinal=0,
           granularity=TICK_GRANULARITY, synthetic=False, bid=1.10, ask=1.12):
    bar = bar if bar is not None else _bar(0)
    return EvaluationPoint(
        bar_index=bar_index, bar=bar,
        eval_bid=bid, eval_ask=ask,
        hit_buy_high=bar.high, hit_buy_low=bar.low,
        hit_sell_high=bar.high, hit_sell_low=bar.low,
        pm_ref_buy=bar.close, pm_ref_sell=bar.close,
        granularity=granularity, tick_ordinal=tick_ordinal,
        is_synthetic_bar_point=synthetic, tick_time=tick_time,
    )


class _Account:
    """口座の代役（記録列の供給元。Account（`simulator/domain/account.py`） と同じ属性名で読む）。"""

    def __init__(self, *, balance=10_000.0, floating_pnl=0.0, margin=0.0,
                 swap=0.0, commission=0.0):
        self.balance = balance
        self.floating_pnl = floating_pnl
        self.margin = margin
        self.swap = swap
        self.commission = commission

    @property
    def equity(self):
        return self.balance + self.floating_pnl + self.swap + self.commission

    def margin_level(self):
        import math

        return math.inf if self.margin == 0 else self.equity / self.margin * 100.0


class _Held:
    """保有玉の代役（`OpenTrade.position` の side / volume だけを読む）。"""

    class _Pos:
        def __init__(self, side, volume):
            self.side = side
            self.volume = volume

    def __init__(self, side, volume):
        self.position = self._Pos(side, volume)


def _unbounded():
    return TraceWindow.of(None, None)


# ---- 1: 列は §6.1 の 18 列ちょうど ----

class TestTheRecordedColumnsAreExactlyTheDeclaredOnes:
    """記録列の集合と、宣言（`COLUMNS`）が単一ソースであること。"""

    def test_the_declared_columns_match_the_design(self):
        # Arrange / Act / Assert: §6.1 の 4 群。
        assert COLUMNS == (
            "time", "bar_index", "tick_ordinal", "granularity", "is_synthetic",
            "eval_bid", "eval_ask",
            "balance", "equity", "floating_pnl", "margin", "margin_level",
            "swap", "commission",
            "open_count", "open_volume_buy", "open_volume_sell",
            "halted",
        )

    def test_the_trace_exposes_exactly_the_declared_columns(self):
        # Arrange
        trace = ColumnarRunTrace(_unbounded())

        # Act
        trace.observe(_point(), _Account(), [], False)

        # Assert: 宣言と実体が一致する（片方だけ増えた形を赤にする）。
        assert tuple(trace.columns) == COLUMNS
        assert all(len(v) == 1 for v in trace.columns.values()), trace.columns

    def test_the_trace_records_no_indicator_and_no_trade_columns(self):
        """§6.2 の複製禁止（指標値・trades を点ごとに写さない）。"""
        # Arrange / Act / Assert
        forbidden = {"indicator", "indicators", "trades", "deals", "series"}
        assert {c for c in COLUMNS if c in forbidden} == set()

    def test_it_is_a_run_trace_port(self):
        # Arrange / Act / Assert
        assert issubclass(ColumnarRunTrace, RunTracePort)


class TestTheColumnValuesComeFromTheObservedArguments:
    """各列の値が、観測が受けた 4 引数のどこから来るか。"""

    def test_the_point_columns_carry_the_point(self):
        # Arrange
        trace = ColumnarRunTrace(_unbounded())
        bar = _bar(3)
        point = _point(bar=bar, tick_time=bar.time, bar_index=7, tick_ordinal=2,
                       bid=1.234, ask=1.236)

        # Act
        trace.observe(point, _Account(), [], False)

        # Assert
        assert trace.columns["bar_index"] == [7]
        assert trace.columns["tick_ordinal"] == [2]
        assert trace.columns["granularity"] == [TICK_GRANULARITY]
        assert trace.columns["is_synthetic"] == [False]
        assert trace.columns["eval_bid"] == [1.234]
        assert trace.columns["eval_ask"] == [1.236]

    def test_the_account_columns_carry_the_account(self):
        # Arrange
        trace = ColumnarRunTrace(_unbounded())
        account = _Account(
            balance=9_000.0, floating_pnl=-250.0, margin=1_000.0,
            swap=-3.0, commission=-7.0,
        )

        # Act
        trace.observe(_point(), account, [], False)

        # Assert: 値の導出（equity / margin_level）は口座が持つ規則をそのまま使う。
        assert trace.columns["balance"] == [9_000.0]
        assert trace.columns["equity"] == [account.equity]
        assert trace.columns["floating_pnl"] == [-250.0]
        assert trace.columns["margin"] == [1_000.0]
        assert trace.columns["margin_level"] == [account.margin_level()]
        assert trace.columns["swap"] == [-3.0]
        assert trace.columns["commission"] == [-7.0]

    @pytest.mark.parametrize(
        "held,count,buy,sell",
        [
            ([], 0, 0.0, 0.0),
            ([_Held("buy", 1.0)], 1, 1.0, 0.0),
            ([_Held("sell", 0.5)], 1, 0.0, 0.5),
            ([_Held("buy", 1.5), _Held("sell", 0.25), _Held("buy", 0.25)], 3, 1.75, 0.25),
        ],
        ids=["none", "one_buy", "one_sell", "mixed"],
    )
    def test_the_holding_columns_carry_the_exact_counts_and_volumes(
        self, held, count, buy, sell
    ):
        """§7.2 通過条件 7: **値**で縛る（`any(n>0)` の存在検査から脱却する）。

        `+1` バイアス・サイドの取り違え・volume の取り落としをそれぞれ赤にする。
        """
        # Arrange
        trace = ColumnarRunTrace(_unbounded())

        # Act
        trace.observe(_point(), _Account(), held, False)

        # Assert
        assert trace.columns["open_count"] == [count]
        assert trace.columns["open_volume_buy"] == [buy]
        assert trace.columns["open_volume_sell"] == [sell]

    @pytest.mark.parametrize("halted", [False, True], ids=["running", "halted"])
    def test_the_halt_column_carries_both_sides(self, halted):
        """§7.2 通過条件 6: **両側**を縛る（常に True の実装も赤にする）。"""
        # Arrange
        trace = ColumnarRunTrace(_unbounded())

        # Act
        trace.observe(_point(), _Account(), [], halted)

        # Assert
        assert trace.columns["halted"] == [halted]


# ---- 2・3・4: `time` 列の出所と導出点 ----

class TestTheTimeColumnComesFromTheSingleDerivation:
    """§6.5.0: `tick_time` → 無ければ `bar.time`。導出は 1 箇所。"""

    def test_a_point_with_a_tick_time_uses_it(self):
        # Arrange
        trace = ColumnarRunTrace(_unbounded())
        bar = _bar(0)
        tick_time = bar.time + np.timedelta64(37, "s")

        # Act
        trace.observe(_point(bar=bar, tick_time=tick_time), _Account(), [], False)

        # Assert: バー時刻ではなくティック時刻（37 秒ぶん進んでいる）。
        assert trace.columns["time"] == [_EPOCH + 37]

    @pytest.mark.parametrize(
        "granularity,tick_ordinal,synthetic",
        [
            (BAR_GRANULARITY, NOT_A_TICK, False),  # BarSchedule の点
            (TICK_GRANULARITY, NOT_A_TICK, True),  # ティック 0 件バーの持ち越し点
        ],
        ids=["bar_schedule_point", "carried_over_point"],
    )
    def test_a_point_without_a_tick_time_falls_back_to_the_bar_time(
        self, granularity, tick_ordinal, synthetic
    ):
        """契約上宣言された不在（ティックを持たない点）への充当であること。

        `epoch_seconds(None)` は `ConfigError` になるため、充当が無ければ
        「ティックを持たない点が 1 つでもある run」は記録できない。
        """
        # Arrange
        trace = ColumnarRunTrace(_unbounded())
        bar = _bar(5)

        # Act
        trace.observe(
            _point(bar=bar, tick_time=None, granularity=granularity,
                   tick_ordinal=tick_ordinal, synthetic=synthetic),
            _Account(), [], False,
        )

        # Assert
        assert trace.columns["time"] == [_EPOCH + 300]

    def test_the_time_column_is_an_epoch_second_integer(self):
        """§7.2 通過条件 5: 型を検定で固定する（`datetime64` を素で載せない）。"""
        # Arrange
        trace = ColumnarRunTrace(_unbounded())
        bar = _bar(0)

        # Act: `bar.time` は `numpy.datetime64`（本 fixture の実型）。
        assert isinstance(bar.time, np.datetime64)
        trace.observe(_point(bar=bar, tick_time=None), _Account(), [], False)
        trace.observe(_point(bar=bar, tick_time=_EPOCH + 12), _Account(), [], False)

        # Assert: どちらの表現からも epoch 秒の int になる。
        assert trace.columns["time"] == [_EPOCH, _EPOCH + 12]
        assert all(type(v) is int for v in trace.columns["time"]), trace.columns["time"]

    def test_an_unsupported_time_representation_fails_loudly(self):
        """推測で解釈しない（`epoch_seconds` の契約をそのまま負う）。"""
        # Arrange
        trace = ColumnarRunTrace(_unbounded())

        # Act / Assert
        with pytest.raises(ConfigError):
            trace.observe(_point(tick_time="2024-01-01"), _Account(), [], False)

    def test_the_module_derives_the_epoch_in_exactly_one_place(self):
        """構文木で `epoch_seconds` の呼出が 1 箇所であることを固定する。

        2 箇所で導出すると、窓判定側と列出力側で別の値になりうる（例外を出さずに
        「窓が通した点の `time` 列が窓の外」が起きる）。
        """
        # Arrange
        import simulator.adapter.trace.columnar_run_trace as mod

        # Act
        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "epoch_seconds"
        ]

        # Assert
        assert len(calls) == 1, [ast.dump(c) for c in calls]

    def test_the_window_is_asked_about_exactly_the_value_that_lands_in_the_time_column(
        self,
    ):
        """窓判定と列出力が**同じ値**を使うこと（導出が 2 箇所に無いことの実測）。

        `tick_time != bar.time` の点だけで構成した入力で測る。片方が `bar.time` を、
        もう片方が `tick_time` を使う実装なら、問われた値と記録された値がずれる。
        """
        # Arrange
        bar = _bar(0)
        offsets = [3, 11, 29, 47]
        asked: "list[int]" = []

        class _AskedWindow:
            start = None
            end = None

            def contains(self, epoch):
                asked.append(epoch)
                # 一部だけ通す（窓が効いている状態で測る）。
                return epoch < _EPOCH + 30

        trace = ColumnarRunTrace(TraceWindow(_AskedWindow()))

        # Act
        for off in offsets:
            trace.observe(
                _point(bar=bar, tick_time=bar.time + np.timedelta64(off, "s")),
                _Account(), [], False,
            )

        # Assert: 問われた値の並びは全点ぶん、記録された値はその部分列（同じ値）。
        assert asked == [_EPOCH + o for o in offsets]
        assert trace.columns["time"] == [e for e in asked if e < _EPOCH + 30]
        # 正の対照: 全通し・全弾きなら「同じ値」の表明が痩せる。
        assert 0 < len(trace.columns["time"]) < len(asked), (trace.columns["time"], asked)


# ---- 5: 非侵襲（記録がエンジンの状態を変えない） ----

class TestTheRecordingDoesNotTouchTheRunState:
    """`RunTracePort` の事後条件 1（引数を読むだけ）。"""

    def test_the_open_trades_list_and_the_account_are_left_untouched(self):
        # Arrange
        trace = ColumnarRunTrace(_unbounded())
        account = _Account(balance=10_000.0, floating_pnl=-5.0)
        before = (account.balance, account.floating_pnl, account.margin)
        held = [_Held("buy", 1.0), _Held("sell", 2.0)]

        # Act
        trace.observe(_point(), account, held, False)

        # Assert: 口座も保有列も 1 つも動いていない。
        assert (account.balance, account.floating_pnl, account.margin) == before
        assert len(held) == 2
        assert [(h.position.side, h.position.volume) for h in held] == [
            ("buy", 1.0), ("sell", 2.0)
        ]

    def test_the_columns_are_plain_lists_not_dataframes(self):
        """列は素の `list`（`simulator/adapter/trace/parquet_trace_store.py` が受け取って初めて DataFrame 化する）。"""
        # Arrange
        trace = ColumnarRunTrace(_unbounded())

        # Act
        trace.observe(_point(), _Account(), [], False)

        # Assert
        assert all(type(v) is list for v in trace.columns.values())


# ---- 6: 技術ドライバの隔離 ----

class TestTheTraceModuleStaysFreeOfTheStorageDrivers:
    def test_the_module_imports_neither_pandas_nor_pyarrow(self):
        # Arrange
        import simulator.adapter.trace.columnar_run_trace as mod

        # Act
        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        # 分岐ではなく内包表記で集める: 入力によって実行経路が変わらないので、
        # 「どの枝を通ったか」に依らず同じ集合が出る（検出範囲は同一）。
        nodes = list(ast.walk(tree))
        imported = {
            alias.name.split(".")[0]
            for n in nodes if isinstance(n, ast.Import) for alias in n.names
        } | {
            n.module.split(".")[0]
            for n in nodes if isinstance(n, ast.ImportFrom) and n.module
        }

        # Assert
        assert imported & {"pandas", "pyarrow"} == set(), imported
        assert imported, "import が 1 件も読めていない（走査が空振り）"


# ---- 計算量: 記録行数 − 窓内の評価点数 = 0 ----

def _run(*, window=None, bar_count=8, strategy=None, tick_model=None, request=None):
    """1 run 実行し `(result, trace, schedule_spy)` を返す。

    窓の述語は**本関数だけが配る**（記録先と計数器へ別々に渡すと、2 つが食い違ったまま
    差 0 が緑になりうる）。トレースには実体の `TraceWindow` を、計数器には同じ境界を
    表す述語を渡し、両者が同じ窓を見ていることを構造で保証する。
    """
    trace_window = window if window is not None else TraceWindow.of(None, None)

    def _inside(point):
        raw = point.tick_time if point.tick_time is not None else point.bar.time
        from simulator.domain.bar_time import epoch_seconds

        return trace_window.contains(epoch_seconds(raw))

    trace = ColumnarRunTrace(trace_window)
    model = tick_model if tick_model is not None else _OneTickPerBar()
    schedule = _CountingSchedule(
        TickSchedule(tick_model=model, pending_lifecycle=False, point_size=_POINT_SIZE),
        window=_inside,
    )
    interactor = RunBacktestInteractor(
        strategy=strategy if strategy is not None else _NullStrategy(),
        indicators=_NullIndicators(),
        tick_model=model,
        schedule=schedule,
        run_tracer=trace,
    )
    result = interactor.execute(
        request if request is not None else _request(_bars(bar_count), config=_config())
    )
    return result, trace, schedule


_FIXTURES = [
    ("plain", _fixture_plain),
    ("empty_tick_bars", _fixture_with_empty_tick_bars),
    ("halting", _fixture_halting),
    # §7.2 通過条件 8: 1 バー複数ティック（`tick_ordinal > 0` の行を作る）。
    ("many_ticks_per_bar", lambda: {
        "strategy": _orders_strategy(), "bar_count": 8, "tick_model": _NTicksPerBar(4),
    }),
]


class TestTheRecordedRowsAreExactlyTheEvaluationPointsInsideTheWindow:
    """発行（記録行）− 使用（窓内の評価点）= 0。"""

    @pytest.mark.parametrize("name,fixture", _FIXTURES, ids=[n for n, _ in _FIXTURES])
    def test_the_recorded_rows_minus_the_points_produced_is_zero(self, name, fixture):
        # Arrange / Act: 窓なし＝生んだ点はすべて記録されるべき。
        _result, trace, schedule = _run(**fixture())

        # Assert
        assert trace.rows - len(schedule.produced) == 0, (trace.rows, len(schedule.produced))
        assert list(zip(trace.columns["bar_index"], trace.columns["tick_ordinal"])) == (
            schedule.produced
        )
        # 正の対照: 0 件なら差 0 は恒真になる。
        assert schedule.produced, "評価点が 0 件（検定が何も測っていない）"
        # すべての列が同じ行数を持つ（1 列だけ落ちる形を赤にする）。
        assert {len(v) for v in trace.columns.values()} == {trace.rows}

    @pytest.mark.parametrize("name,fixture", _FIXTURES, ids=[n for n, _ in _FIXTURES])
    def test_no_evaluation_point_is_recorded_twice(self, name, fixture):
        # Arrange / Act
        _result, trace, _schedule = _run(**fixture())

        # Assert: `(bar_index, tick_ordinal)` は主キーである。
        keys = list(zip(trace.columns["bar_index"], trace.columns["tick_ordinal"]))
        assert len(keys) == len(set(keys)), keys
        # 正の対照: 記録 0 件なら重複なしは恒真になる。
        assert keys, "記録が 0 件（1 点 1 回の表明が恒真になっている）"

    def test_the_many_ticks_fixture_actually_produces_intrabar_ordinals(self):
        """§7.2 通過条件 8 の正の対照: `tick_ordinal > 0` の行が実在すること。

        全 fixture が `points_per_bar=1` のままだと、主キーの一意性は `bar_index` だけで
        成立してしまい、`tick_ordinal` を取り落とす実装が素通りする。
        """
        # Arrange / Act
        _result, trace, _schedule = _run(
            strategy=_orders_strategy(), bar_count=8, tick_model=_NTicksPerBar(4)
        )

        # Assert
        ordinals = set(trace.columns["tick_ordinal"])
        assert {0, 1, 2, 3} <= ordinals, ordinals
        # 同一バーに複数行が在る。
        from collections import Counter

        per_bar = Counter(trace.columns["bar_index"])
        assert max(per_bar.values()) >= 4, per_bar

    def test_the_window_keeps_only_the_points_inside_it(self):
        # Arrange: 8 バー（1 分足）の中央付近だけを残す窓。
        window = TraceWindow.of(_EPOCH + 120, _EPOCH + 300)

        # Act
        _result, trace, schedule = _run(window=window, **_fixture_plain())

        # Assert
        assert trace.rows - len(schedule.in_window) == 0
        assert set(trace.columns["bar_index"]) == {2, 3, 4}
        # 窓の外は 1 行も記録されない。
        outside = set(schedule.produced) - set(schedule.in_window)
        assert outside, "窓の外の評価点が 0 件（窓が効いていない fixture）"
        keys = set(zip(trace.columns["bar_index"], trace.columns["tick_ordinal"]))
        assert keys & outside == set()
        # 正の対照: 窓内が空なら上のすべてが恒真になる。
        assert schedule.in_window, "窓内の評価点が 0 件（検定が何も測っていない）"
        # 記録された `time` はすべて窓の中にある（列と窓判定の一致）。
        assert all(_EPOCH + 120 <= t < _EPOCH + 300 for t in trace.columns["time"])

    def test_the_recorded_rows_do_not_grow_with_the_number_of_bars(self):
        """オーダー: 記録量は窓が決め、run 長が決めない。"""
        # Arrange / Act
        window_bounds = (_EPOCH + 120, _EPOCH + 300)
        measured = {}
        for bar_count in (12, 48):
            _result, trace, schedule = _run(
                window=TraceWindow.of(*window_bounds),
                bar_count=bar_count,
                strategy=_orders_strategy(),
            )
            keys = set(zip(trace.columns["bar_index"], trace.columns["tick_ordinal"]))
            outside = set(schedule.produced) - set(schedule.in_window)
            assert keys & outside == set(), bar_count
            measured[bar_count] = (trace.rows, keys)

        # 正の対照: 両方 0 件なら差 0 は恒真になる。
        assert measured[12][0] > 0, "記録が 0 件（オーダーの表明が恒真になっている）"
        # Assert: 入力を 4 倍にしても記録量は増えない。
        assert measured[48][0] - measured[12][0] == 0, {
            k: v[0] for k, v in measured.items()
        }
        assert measured[48][1] == measured[12][1]


class TestTheRecordedValuesTrackTheRunItself:
    """記録された値が run の事実と結びついていること（列が飾りでないこと）。"""

    def test_the_equity_column_is_the_equity_curve_of_the_run(self):
        # Arrange / Act
        result, trace, _schedule = _run(**_fixture_plain())

        # Assert: 評価点 1 つにつき equity がちょうど 1 点積まれる。
        assert trace.columns["equity"] == result.equity_curve
        # 正の対照: equity が動かない run では何も測れていない。
        assert len(set(trace.columns["equity"])) > 1, trace.columns["equity"]

    def test_the_holding_columns_agree_with_each_other(self):
        """open_count と `open_volume_*` が同じ保有列から出ていること。

        本 fixture の発注はすべて 1.0 lot・**同方向**なので保有が積み上がり、件数と
        量の総和は常に一致する。open_count に `+1` バイアスを入れる変異、および
        件数を定数で返す変異が、ここで赤になる。

        `_fixture_plain`（買い→売り→買い）を使わない理由: 反対売買は reverse 決済に
        なるため保有数が常に 1 のままで、「定数を返す実装」と区別できない（実測）。
        """
        # Arrange / Act: 同方向の成行を 3 本積む。
        _result, trace, _schedule = _run(
            strategy=_OrdersPerBar(
                {0: [_market("buy")], 2: [_market("buy")], 4: [_market("buy")]}
            ),
            bar_count=8,
        )

        # Assert
        for n, buy, sell in zip(
            trace.columns["open_count"],
            trace.columns["open_volume_buy"],
            trace.columns["open_volume_sell"],
        ):
            assert buy + sell == pytest.approx(n * 1.0), (n, buy, sell)
        # 正の対照: 保有が 1 件も無い run／保有数が動かない run では上は恒真になる
        # （定数を返す実装でも「件数×1.0」が成り立ってしまう）。
        assert max(trace.columns["open_count"]) > 0, trace.columns["open_count"]
        assert len(set(trace.columns["open_count"])) > 1, trace.columns["open_count"]

    def test_the_halt_column_is_false_everywhere_in_a_run_that_never_halts(self):
        """§7.2 通過条件 6 の片側: 「常に True」を赤にする。"""
        # Arrange / Act
        _result, trace, _schedule = _run(**_fixture_plain())

        # Assert
        assert set(trace.columns["halted"]) == {False}
        # 正の対照: 行が 0 件なら上は恒真になる。
        assert trace.rows > 0

    def test_the_halt_column_turns_true_and_stays_true_in_a_halting_run(self):
        """もう片側: 「常に False」を赤にする。"""
        # Arrange / Act
        result, trace, _schedule = _run(**_fixture_halting())

        # Assert: 正の対照（run が実際に halt している）。
        assert [t.exit_reason for t in result.trades] == ["stop_out"]
        assert set(trace.columns["halted"]) == {False, True}, trace.columns["halted"]
        # halt は一度立てば戻らない。
        assert trace.columns["halted"] == sorted(trace.columns["halted"])

    def test_the_synthetic_flag_marks_the_carried_over_points_only(self):
        # Arrange / Act
        _result, trace, schedule = _run(**_fixture_with_empty_tick_bars())

        # Assert
        marked = {
            b for b, s in zip(trace.columns["bar_index"], trace.columns["is_synthetic"]) if s
        }
        assert marked == {b for b, _o in schedule.synthetic}
        # 正の対照: 合成点が 0 件なら上は恒真になる。
        assert schedule.synthetic, "合成バー点が 0 件（fixture が効いていない）"

    def test_a_run_with_zero_tick_bars_still_records_a_time_for_every_row(self):
        """§6.5.0 の充当が実 run で効いていること（`ConfigError` にならない）。"""
        # Arrange / Act
        _result, trace, schedule = _run(**_fixture_with_empty_tick_bars())

        # Assert
        assert len(trace.columns["time"]) == len(schedule.produced)
        assert all(type(t) is int for t in trace.columns["time"])
        assert schedule.synthetic, "持ち越し点が 0 件（充当が試されていない）"
