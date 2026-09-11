"""`derive_trace_events`（RUN_TRACE_BASIC_DESIGN §9.3/§9.6）。

trace は**状態列**であって事象列ではない。「いつ halt したか」「いつ建玉が増減したか」
「いつ維持率が閾値を割ったか」は状態の**遷移**であり、列そのものには書かれていない。
本 usecase はその導出だけを負う（何を返すかの決定は query_trace の関心）。

**計算量テスト（絶対命令）**: 状態列を **1 回だけ**走査する。事象種別ごとに全走査し
直す実装は、出力が正しいまま走査回数が種別数倍になる（状態検証では原理的に落ちない）。
測るのは時間ではなく**走査回数と要素参照回数**である。

用語（front の禁止語彙を持ち込まない）:
    `removed_ui_vocabulary_gate.test.js` が front の `js/adapter/front/*.js` から
    trailing / partial の語をコメント含め排除している。事象名にこれらの語を
    使わない（front がそのまま表示語彙として受け取るため）。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.usecase.derive_trace_events import (
    EVENT_KINDS,
    REQUIRED_COLUMNS,
    RESUME,
    TraceEvent,
    HALT,
    MARGIN_FLOOR_BREACH,
    POSITION_CLOSED,
    POSITION_OPENED,
    RESUME,
    DeriveTraceEventsInteractor,
)

_T0 = 1_704_067_200_000  # epoch ミリ秒


def _columns(*, halted, open_count, margin_level, step_ms=1000):
    rows = len(halted)
    assert rows == len(open_count) == len(margin_level)
    return {
        "time": [_T0 + i * step_ms for i in range(rows)],
        "halted": list(halted),
        "open_count": list(open_count),
        "margin_level": list(margin_level),
    }


def _derive(columns, *, floor=None):
    return DeriveTraceEventsInteractor().execute(columns, margin_level_floor=floor)


def _kinds(events):
    return [e.kind for e in events]


# ---- 1: 遷移だけを事象にする ----

class TestTheHaltTransitionIsAnEvent:
    def test_the_moment_halt_turns_on_is_reported_once(self):
        # Arrange: halt は 3 点目で立ち、以後立ちっぱなし。
        columns = _columns(
            halted=[False, False, True, True, True],
            open_count=[0, 0, 0, 0, 0],
            margin_level=[1_000.0] * 5,
        )

        # Act
        events = _derive(columns)

        # Assert: 状態が続くあいだ毎点報告しない（遷移は 1 回）。
        assert _kinds(events) == [HALT]
        assert events[0].time == _T0 + 2 * 1000

    def test_a_run_that_never_halts_reports_no_halt(self):
        """両側拘束: 「常に halt」変異も「常に無事象」変異も赤にする。"""
        # Arrange
        columns = _columns(
            halted=[False] * 4, open_count=[0] * 4, margin_level=[1_000.0] * 4
        )

        # Act
        events = _derive(columns)

        # Assert
        assert HALT not in _kinds(events)
        assert events == ()

    def test_a_run_that_starts_halted_reports_it_at_the_first_row(self):
        """先頭行の状態は「直前が無い」——発明しない。

        直前を `False` と仮定すると「halt していない状態から始まった」という**別の
        事実**を作る。halt 済みで始まる窓（窓を halt 後に切った場合）は実在する。
        """
        # Arrange
        columns = _columns(
            halted=[True, True], open_count=[0, 0], margin_level=[1_000.0] * 2
        )

        # Act
        events = _derive(columns)

        # Assert
        assert _kinds(events) == [HALT]
        assert events[0].time == _T0

    def test_the_reverse_transition_is_reported_too(self):
        # Arrange
        columns = _columns(
            halted=[False, True, False],
            open_count=[0, 0, 0],
            margin_level=[1_000.0] * 3,
        )

        # Act / Assert
        assert _kinds(_derive(columns)) == [HALT, RESUME]


class TestThePositionCountChangeIsAnEvent:
    def test_an_increase_and_a_decrease_are_distinguished(self):
        # Arrange
        columns = _columns(
            halted=[False] * 5,
            open_count=[0, 1, 2, 1, 1],
            margin_level=[1_000.0] * 5,
        )

        # Act
        events = _derive(columns)

        # Assert
        assert _kinds(events) == [POSITION_OPENED, POSITION_OPENED, POSITION_CLOSED]
        # 値そのものを持つ（front が突合できるように）。
        assert [(e.previous, e.value) for e in events] == [(0, 1), (1, 2), (2, 1)]

    def test_a_flat_count_produces_nothing(self):
        # Arrange
        columns = _columns(
            halted=[False] * 4, open_count=[2, 2, 2, 2], margin_level=[1_000.0] * 4
        )

        # Act / Assert
        assert _derive(columns) == ()

    def test_a_jump_of_more_than_one_is_still_a_single_event(self):
        """建玉が一度に 2 つ増えるのは 1 つの遷移である（件数を発明しない）。"""
        # Arrange
        columns = _columns(
            halted=[False, False], open_count=[0, 3], margin_level=[1_000.0] * 2
        )

        # Act
        events = _derive(columns)

        # Assert
        assert _kinds(events) == [POSITION_OPENED]
        assert (events[0].previous, events[0].value) == (0, 3)


class TestTheMarginLevelFloorBreachIsAnEvent:
    def test_crossing_below_the_floor_is_reported_once(self):
        # Arrange: 閾値 100.0 を 3 点目で下抜け、以後下のまま。
        columns = _columns(
            halted=[False] * 5,
            open_count=[1] * 5,
            margin_level=[300.0, 200.0, 90.0, 80.0, 70.0],
        )

        # Act
        events = _derive(columns, floor=100.0)

        # Assert
        assert _kinds(events) == [MARGIN_FLOOR_BREACH]
        assert events[0].time == _T0 + 2 * 1000
        assert events[0].value == 90.0

    def test_without_a_floor_no_breach_is_invented(self):
        """閾値が無ければ「割れ」は定義できない——既定値を発明しない（§7）。"""
        # Arrange
        columns = _columns(
            halted=[False] * 3, open_count=[1] * 3, margin_level=[300.0, 50.0, 10.0]
        )

        # Act / Assert
        assert _derive(columns, floor=None) == ()

    def test_recovering_above_the_floor_re_arms_the_event(self):
        # Arrange
        columns = _columns(
            halted=[False] * 4,
            open_count=[1] * 4,
            margin_level=[300.0, 50.0, 300.0, 50.0],
        )

        # Act / Assert: 割れは 2 回（同じ状態が続くあいだは 1 回）。
        assert _kinds(_derive(columns, floor=100.0)) == [
            MARGIN_FLOOR_BREACH, MARGIN_FLOOR_BREACH
        ]

    def test_the_boundary_is_strictly_below(self):
        """閾値ちょうどは「割れていない」（境界の向きを固定する）。"""
        # Arrange
        columns = _columns(
            halted=[False] * 2, open_count=[1] * 2, margin_level=[300.0, 100.0]
        )

        # Act / Assert
        assert _derive(columns, floor=100.0) == ()

    def test_an_infinite_margin_level_is_not_a_breach(self):
        """建玉 0 の点は維持率が無限大になる（`Account.margin_level`）。"""
        # Arrange
        columns = _columns(
            halted=[False] * 2,
            open_count=[0, 0],
            margin_level=[float("inf"), float("inf")],
        )

        # Act / Assert
        assert _derive(columns, floor=100.0) == ()


class TestTheKindsAreDeclaredInOnePlace:
    def test_every_derived_kind_belongs_to_the_declaration(self):
        # Arrange: 全種別が出る入力。
        columns = _columns(
            halted=[False, True, False, False],
            open_count=[0, 1, 0, 0],
            margin_level=[300.0, 300.0, 300.0, 50.0],
        )

        # Act
        events = _derive(columns, floor=100.0)

        # Assert: 宣言の外の名前を作らない。
        assert set(_kinds(events)) <= set(EVENT_KINDS)
        # 正の対照: 宣言の全種別が実際に導出できる（死んだ宣言を作らない）。
        assert set(_kinds(events)) == set(EVENT_KINDS)

    @pytest.mark.parametrize("forbidden", ["trailing", "partial"])
    def test_the_kind_names_avoid_the_front_forbidden_vocabulary(self, forbidden):
        """front の `removed_ui_vocabulary_gate.test.js` が排除している語を使わない。"""
        assert all(forbidden not in kind for kind in EVENT_KINDS), EVENT_KINDS


# ---- 2: 計算量テスト（絶対命令・§9.6） ----
#
# **計器は 2 つ要る。「発行総数」だけでは不十分**（工程 5 再レビューで実測確定）。
#
# 初版の Spy は注入列への `__iter__` / __getitem__ を数えた。その計器は**実装が先に私的な
# 写しを作れば以後の浪費を観測できない**——実測: 列を 1 度写してから種別数（5）ぶん写しを
# 全走査する変異（出力は 1 bit も変わらない）が **20 passed** で通った。
#
# 第 2 版は計器 2 を「構文木の loop 数（`loops == 1`）」で実装したが、**両方向に誤ることを
# 自分で実測した**（下表）。`loops == 1` は実装詳細を仕様へ昇格させる形であり、CLAUDE.md が
# 名指しで禁じた「回数そのものを期待値に焼き込む」（ISSUE-450 の前例）と同型である。
#
#   | 変異                                        | loops == 1 | 連続発行の最大 |
#   |---------------------------------------------|-----------|---------------|
#   | honest                                       | 緑         | 1             |
#   | 浪費を助力メソッドへ移す（execute の loop は 1） | **緑（偽陰性）** | **200（赤）** |
#   | 列名を宣言から導いて遅延 zip（浪費 0・正当）     | **赤（偽陽性）** | 1（緑）        |
#
#   実測（行数 200・要る列 4 本）: honest 発行 800 / 連続 1、正当な書き換え 発行 800 / 連続 1、
#   写し＋助力メソッド 発行 800 / 連続 200。いずれも events は 250 で**出力は同一**。
#
# 第 3 版（現行）の 2 計器:
#
#   計器 1（発行総数）: 1 要素ずつ供給する **one-shot generator** を注入し
#       `発行 − 行数 × 要る列数 = 0` を表明する。期待値は宣言（`REQUIRED_COLUMNS`）と
#       入力の大きさから**導出**し、回数リテラルを焼き込まない。**供給元の再走査**は
#       一度しか回せない性質そのものが赤にする。
#   計器 2（同一列からの連続発行の最大 == 1）: 発行のたびに列名を共有ログへ積み、同じ列が
#       連続して発行した最大長を見る。列を materialise すると**ブロック排出**（同一列が
#       行数ぶん連続）になるため、写しを経由しても浪費が観測できる。上限 1 は
#       「要る列が 2 つ以上ある」という宣言から導かれ、リテラルの回数ではない。
#       遅延 `zip` は列を交互に引くので、正当な書き換えを拒まない。
#
# **検出力の自己検定は 2 形（インライン形・助力メソッド形）置く**。見本を助力メソッド形に
# すると検出境界が足りないときに自己検定が自分から赤になる（第 2 版がそうだった）。
# 併せて**偽陽性の番人**（正当な書き換えを赤にしない）も常設する。
# 先例は `sim_ui/web/tests/sim_trace_block.test.js` と
# `simulator/replay_ui/tests/unit/test_static_file_server_allowlist_complexity.py`。


class _OneShotColumn:
    """1 要素ずつ供給し、**発行のたびに列名を共有ログへ積む**列（2 度目の走査を許さない）。

    ログが計器 1（総数）と計器 2（連続長）の両方の素になる。`list()` で写されても、
    写しに入るのは**この列が発行した要素**なので、写す行為そのものが
    「同一列の連続発行 = 行数」としてログに現れる。
    """

    def __init__(self, values, log: "list[str]", name: str) -> None:
        self._values = list(values)
        self._log = log
        self._name = name
        self._walked = False

    def __iter__(self):
        if self._walked:
            raise AssertionError(f"列 {self._name} を 2 度走査した（供給元の再走査）")
        self._walked = True

        def issue():
            for value in self._values:
                self._log.append(self._name)
                yield value

        return issue()

    def __len__(self):
        return len(self._values)


def _issuing_columns(rows: int, log: "list[str]"):
    """`REQUIRED_COLUMNS` を one-shot 列で供給する（事象が複数種別出る形）。"""
    raw = {
        "time": [_T0 + i * 1000 for i in range(rows)],
        "halted": [i >= rows - 2 for i in range(rows)],
        "open_count": [i % 3 for i in range(rows)],
        "margin_level": [300.0 if i % 4 else 50.0 for i in range(rows)],
    }
    assert set(raw) == set(REQUIRED_COLUMNS), (set(raw), set(REQUIRED_COLUMNS))
    return {name: _OneShotColumn(v, log, name) for name, v in raw.items()}


def _max_consecutive_issues(log: "list[str]") -> int:
    """同一列が連続して発行した最大長（1 なら列を交互に引いている＝写していない）。"""
    if not log:
        return 0
    best = run = 1
    for previous, current in zip(log, log[1:]):
        run = run + 1 if current == previous else 1
        best = max(best, run)
    return best


def _measure(interactor, rows: int):
    """`(発行総数, 同一列の連続発行の最大, 事象)` を返す。"""
    log: "list[str]" = []
    events = interactor.execute(_issuing_columns(rows, log), margin_level_floor=100.0)
    return len(log), _max_consecutive_issues(log), events


class TestNoElementIsIssuedAndThenDiscarded:
    """計器 1: 発行 − 使用 = 0（行数 × 要る列数 が「使用」である）。"""

    @pytest.mark.parametrize("rows", [40, 400])
    def test_the_issued_element_count_equals_the_rows_times_the_needed_columns(
        self, rows
    ):
        # Arrange / Act
        issued, _consecutive, events = _measure(DeriveTraceEventsInteractor(), rows)

        # Assert: 期待値は宣言と入力から導出する（回数リテラルを書かない）。
        used = rows * len(REQUIRED_COLUMNS)
        assert issued - used == 0, (issued, used)
        # 正の対照: 事象が 0 件・種別 1 つなら「浪費の不在」の表明が痩せる。
        assert len(events) > 0
        assert len({e.kind for e in events}) >= 3, {e.kind for e in events}

    def test_the_per_row_issuance_does_not_change_with_the_run_length(self):
        """オーダーの表明（2 点）。行数を 10 倍にしても 1 行あたりの発行は同じ。"""
        # Arrange / Act
        per_row = [
            _measure(DeriveTraceEventsInteractor(), rows)[0] / rows
            for rows in (40, 400)
        ]

        # Assert
        assert per_row[0] == per_row[1], per_row
        assert per_row[0] > 0


class TestNoColumnIsMaterialisedAndRescanned:
    """計器 2: 同一列からの連続発行の最大が 1。

    列を materialise（`list()` で写す）と、その列だけが行数ぶん連続して発行される
    （ブロック排出）。交互に引いている限り連続長は 1 である。上限 1 は
    「要る列が 2 つ以上ある」という宣言から導かれる。

    **射程（自己レビューで測った限界・隠さない）**: 本計器は「再走査」ではなく
    **materialise そのもの**を赤にする。実測: 1 列だけ写して再走査しない実装
    （捨てる要素は 0＝CLAUDE.md の等式は破れていない）も連続発行 200 で赤になる。
    したがって計器 2 は §9.6 の文言（「1 回だけ走査する」）より**厳しい**。

    その厳しさが production で偽陽性にならない根拠（機械で固定済みの前提）:
        供給側（`simulator/adapter/trace/parquet_trace_store.py`）は `to_list()` で
        **素の list** を返す。これは
        `simulator/tests/integration/test_parquet_trace_store_read.py` の
        test_the_values_are_plain_lists_not_frames が `type(v) is list` で固定している。
        既に list である列を `list()` で写す行為は production では常に冗長であり、
        「materialise を禁じる」は「冗長な写しを禁じる」と一致する。
        前提が崩れたら（供給が遅延列に変わったら）本計器の射程を再検討する必要がある。
    """

    @pytest.mark.parametrize("rows", [40, 400])
    def test_the_columns_are_drawn_in_lockstep(self, rows):
        # Arrange: 上限 1 が意味を持つのは要る列が 2 本以上あるときだけである。
        assert len(REQUIRED_COLUMNS) >= 2, REQUIRED_COLUMNS

        # Act
        issued, consecutive, events = _measure(DeriveTraceEventsInteractor(), rows)

        # Assert
        assert consecutive == 1, consecutive
        # 正の対照: 発行も事象も空でない（空なら連続長 0/1 が恒真になる）。
        assert issued == rows * len(REQUIRED_COLUMNS)
        assert len(events) > 0


# ---- 3: 検出力の自己検定（検出器が空振りしていないことの表明） ----
#
# 浪費の見本は **2 形**置く（インライン形・助力メソッド形）。第 2 版の計器（`loops == 1`）は
# インライン形だけを捕らえ、助力メソッド形を素通りさせた（実測 23 passed）。見本を 2 形
# 置いておけば、計器を差し替えたときに検出境界の不足がその場で露出する。

class _WastefulByRescanningInline:
    """浪費形 1: 列を写し、**その場の二重ループ**で種別数ぶん再走査する。"""

    def execute(self, columns, *, margin_level_floor):
        copy = {name: list(values) for name, values in columns.items()}
        for _kind in EVENT_KINDS:
            for _value in copy["halted"]:
                pass
            for _value in copy["open_count"]:
                pass
            for _value in copy["margin_level"]:
                pass
        return DeriveTraceEventsInteractor().execute(
            copy, margin_level_floor=margin_level_floor
        )


class _WastefulByRescanningViaHelpers:
    """浪費形 2: 列を写し、**種別ごとの助力メソッド**で再走査する。

    「種別ごとに 1 メソッド」は最も自然な分解であり、インライン形より起きやすい。
    第 2 版の計器はこの形を素通りさせた（`execute` の loop 数は 1 のまま）。
    """

    def _scan(self, column):
        for _value in column:
            pass

    def _materialise(self, columns):
        return {name: list(values) for name, values in columns.items()}

    def execute(self, columns, *, margin_level_floor):
        copy = self._materialise(columns)
        self._scan(copy["halted"])
        self._scan(copy["open_count"])
        self._scan(copy["margin_level"])
        self._scan(copy["halted"])
        self._scan(copy["open_count"])
        return DeriveTraceEventsInteractor().execute(
            copy, margin_level_floor=margin_level_floor
        )


class _HonestButRestructured:
    """**浪費 0 の正当な書き換え**: 列名を宣言から導いて遅延 `zip` する。

    偽陽性の番人である。第 2 版の計器（構文木の loop 数）はこれを赤にした（実測 2 failed）
    ——正当な改善を拒む検定は、改善を諦めさせるか検定を緩めさせるかのどちらかになる。

    **本番実装へ委譲しない**（自己レビューで是正）: 当初は列の並びを直してから
    `DeriveTraceEventsInteractor` へ委譲していたが、それでは測っているのが本番実装で
    あって「別の書き方」ではない——番人として痩せていた（計器を N7 の形で誤る実装へ
    差し替えても、この番人は気づけない）。導出をここで書き下し、**宣言から導いた遅延
    `zip`** という当該の形そのものを再現する。出力が本番と一致することは下で表明する。
    """

    def execute(self, columns, *, margin_level_floor):
        # 列名を宣言から導き、反復子を遅延 zip する（手書きの並びを持たない）。
        rows = zip(*(iter(columns[name]) for name in REQUIRED_COLUMNS))
        events: "list[TraceEvent]" = []
        first = True
        previous_halted = previous_count = previous_below = None
        for time, halted, open_count, margin_level in rows:
            below = (
                margin_level_floor is not None and margin_level < margin_level_floor
            )
            if first:
                if halted:
                    events.append(TraceEvent(time, HALT, None, halted))
                if below:
                    events.append(
                        TraceEvent(time, MARGIN_FLOOR_BREACH, None, margin_level)
                    )
                first = False
            else:
                if halted != previous_halted:
                    events.append(
                        TraceEvent(
                            time, HALT if halted else RESUME, previous_halted, halted
                        )
                    )
                if open_count != previous_count:
                    events.append(
                        TraceEvent(
                            time,
                            POSITION_OPENED
                            if open_count > previous_count
                            else POSITION_CLOSED,
                            previous_count,
                            open_count,
                        )
                    )
                if below and not previous_below:
                    events.append(
                        TraceEvent(
                            time, MARGIN_FLOOR_BREACH, previous_below, margin_level
                        )
                    )
            previous_halted, previous_count, previous_below = halted, open_count, below
        return tuple(events)


class TestTheWasteDetectorsActuallyCatchWastefulImplementations:
    """検出器そのものの検定。これが無いと「緑」が空振りと区別できない。"""

    @pytest.mark.parametrize(
        "wasteful",
        [_WastefulByRescanningInline(), _WastefulByRescanningViaHelpers()],
        ids=["inline", "via_helpers"],
    )
    def test_the_lockstep_gate_catches_materialising_and_rescanning(self, wasteful):
        """計器 2 が写し＋再走査を赤にする（インライン形・助力メソッド形の両方）。"""
        # Arrange
        rows = 200

        # Act
        _issued, consecutive, _events = _measure(wasteful, rows)

        # Assert: 写すとブロック排出になる（連続長が 1 を大きく超える）。
        assert consecutive > 1, consecutive
        # 写した列の連続長は行数に達する（写しであることの表明）。
        assert consecutive == rows, consecutive

    @pytest.mark.parametrize(
        "wasteful",
        [_WastefulByRescanningInline(), _WastefulByRescanningViaHelpers()],
        ids=["inline", "via_helpers"],
    )
    def test_the_wasteful_implementations_produce_identical_output(self, wasteful):
        """浪費実装の出力が 1 bit も変わらないこと（状態検証では原理的に落ちない）。

        これが本検定群の存在理由である——出力を見る検定は浪費を**永久に**通す。
        """
        # Arrange
        columns = _columns(
            halted=[False, True, False, False],
            open_count=[0, 1, 0, 0],
            margin_level=[300.0, 300.0, 300.0, 50.0],
        )

        # Act
        honest = DeriveTraceEventsInteractor().execute(
            dict(columns), margin_level_floor=100.0
        )
        wasted = wasteful.execute(dict(columns), margin_level_floor=100.0)

        # Assert
        assert honest == wasted
        assert len(honest) > 0

    def test_the_issuance_gate_catches_reiterating_the_source(self):
        """計器 1 が「供給元を引き直す」実装を赤にする（写しを作らない浪費形）。"""
        # Arrange
        class _Reiterating:
            def execute(self, columns, *, margin_level_floor):
                for _kind in EVENT_KINDS:
                    for _value in columns["halted"]:
                        pass
                return DeriveTraceEventsInteractor().execute(
                    columns, margin_level_floor=margin_level_floor
                )

        # Act / Assert: one-shot 列は 2 度目の走査を拒む。
        with pytest.raises(AssertionError, match="2 度走査した"):
            _measure(_Reiterating(), 40)

    def test_the_gates_do_not_reject_an_honest_restructuring(self):
        """**偽陽性の番人**: 浪費 0 の正当な書き換えは両計器を通る。

        第 2 版の計器（構文木の loop 数）はこれを赤にした。検定が正当な改善を拒むと、
        実装を歪めるか検定を緩めるかのどちらかになる——どちらも規約を腐らせる。
        """
        # Arrange
        rows = 200

        # Act
        issued, consecutive, events = _measure(_HonestButRestructured(), rows)

        # Assert: honest と同じ測定値・同じ出力になる（別の書き方であって別の振る舞いでない）。
        honest = _measure(DeriveTraceEventsInteractor(), rows)
        assert (issued, consecutive) == (honest[0], honest[1])
        assert issued - rows * len(REQUIRED_COLUMNS) == 0
        assert consecutive == 1
        assert events == honest[2]
        assert len(events) > 0

    def test_the_false_positive_guard_is_an_independent_implementation(
        self, monkeypatch
    ):
        """番人が本番実装へ委譲していないこと（委譲していたら番人として痩せている）。

        当初は `DeriveTraceEventsInteractor` へ委譲していたため、測っていたのは本番実装で
        あって「別の書き方」ではなかった——計器を N7 の形で誤る実装へ差し替えても、
        その番人は気づけない。番人は独立した導出を持たなければならない。

        ソース走査ではなく**実行**で確かめる: 本番実装を呼べば必ず落ちる状態にしてから
        番人を動かす。委譲していれば例外になり、独立していれば導出できる。
        """
        # Arrange: 本番実装へ触れたら分かるようにする。
        def delegated(*_args, **_kwargs):
            raise AssertionError(
                "偽陽性の番人が本番実装へ委譲している（独立した導出を持つこと）"
            )

        monkeypatch.setattr(DeriveTraceEventsInteractor, "execute", delegated)

        # Act
        issued, consecutive, events = _measure(_HonestButRestructured(), 40)

        # Assert: 本番に触れずに導出できている。
        assert consecutive == 1, consecutive
        assert issued == 40 * len(REQUIRED_COLUMNS)
        # 正の対照: 事象を実際に作っている（空なら何も導出していない）。
        assert len(events) > 0
        assert len({e.kind for e in events}) >= 2, {e.kind for e in events}

    def test_the_lockstep_gate_is_not_vacuous(self):
        """正の対照: 計器 2 が実際にログを積んでいる（0 のまま通していない）。"""
        # Arrange / Act
        issued, consecutive, _events = _measure(DeriveTraceEventsInteractor(), 40)

        # Assert
        assert issued == 40 * len(REQUIRED_COLUMNS)
        assert consecutive == 1
        # 計器そのものの健全性（写しのログなら行数を返す）。
        assert _max_consecutive_issues(["a"] * 7 + ["b"] * 7) == 7
        assert _max_consecutive_issues(["a", "b"] * 7) == 1
        assert _max_consecutive_issues([]) == 0
