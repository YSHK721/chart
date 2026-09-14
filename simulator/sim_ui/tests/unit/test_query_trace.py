"""`query_trace`（RUN_TRACE_BASIC_DESIGN §9.2/§9.3/§9.6）。

責務は「**何を返すか**」——窓・列の解釈と、返す量の決定である。読み方
（`simulator/adapter/trace/parquet_trace_store.py`）とも事象の意味
（`simulator/sim_ui/usecase/derive_trace_events.py`）とも別の関心である。

**計算量テスト（絶対命令）**: 返す量の上限判定に**行を読まない**。読んでから
「多すぎた」と断るのは「作ってから捨てる」形そのものであり、実測 1,036,394 行の
成果物に対して 100MB を materialise してから捨てることになる。

**返す量の設計（実測に基づく・2026-09-10）**:
    実ティック 1 ヶ月 run の trace_points.parquet は 1,036,394 行 / 9.7MB。
    画面が描くのは数千点である。全量を渡して front で捨てる形は絶対命令に反する。
    サーバは窓を受けて必要ぶんだけ返し、窓が広すぎるときは**明示エラー**で断る。

    間引き（同一秒を代表値へ潰す・N 点ごとに拾う）は採らない。§9.0 が是正した欠陥と
    同型で、equity / margin_level の谷が消えて DD 分析が壊れる（既存 front の
    `chart.js` `dedupeCurve` がまさにその形）。窓を狭めるのが正しい絞り方である。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.usecase.derive_trace_events import (
    HALT,
    MARGIN_FLOOR_BREACH,
    POSITION_OPENED,
    DeriveTraceEventsInteractor,
)
from simulator.sim_ui.usecase.query_trace import (
    ANALYSIS_COLUMNS,
    MAX_RETURNED_ROWS,
    QueryTraceInteractor,
    TraceWindowTooWideError,
)
from simulator.sim_ui.usecase.trace_query_ports import TraceExtent, TracePointsPort

_T0 = 1_704_067_200_000
_STEP = 1000
_JOB = "0123456789abcdef01234567"


class _FakeSource(TracePointsPort):
    """Port の代役。**何を要求されたか**を記録する Test Spy でもある。

    実データは持たず、要求された窓・列だけを返す。ここで測りたいのは
    「usecase が何を読ませたか」であり、parquet の読み方ではない。
    """

    def __init__(self, *, rows=100, floor=100.0, deposit=10_000.0):
        self._rows = rows
        self._floor = floor
        self._deposit = deposit
        self.read_calls: "list[dict]" = []
        self.count_calls: "list[dict]" = []
        self.extent_calls = 0

    # --- 生成 ---------------------------------------------------------

    def _row(self, i: int) -> "dict":
        return {
            "time": _T0 + i * _STEP,
            "balance": 10_000.0,
            "equity": 10_000.0 - (i % 7) * 10.0,
            "margin": 100.0,
            "margin_level": 50.0 if i == 5 else 1_000.0,
            "open_count": 1 if i >= 3 else 0,
            "halted": i >= 90,
        }

    def _indices(self, start, end):
        return [
            i
            for i in range(self._rows)
            if (start is None or _T0 + i * _STEP >= start)
            and (end is None or _T0 + i * _STEP < end)
        ]

    # --- TracePointsPort ----------------------------------------------

    def extent(self, job_id):
        self.extent_calls += 1
        return TraceExtent(
            rows=self._rows,
            first_time=_T0,
            last_time=_T0 + (self._rows - 1) * _STEP,
            initial_deposit=self._deposit,
            margin_level_floor=self._floor,
        )

    def count(self, job_id, *, start=None, end=None):
        self.count_calls.append({"start": start, "end": end})
        return len(self._indices(start, end))

    def read(self, job_id, *, columns, start=None, end=None):
        self.read_calls.append(
            {"columns": tuple(columns), "start": start, "end": end}
        )
        indices = self._indices(start, end)
        return {name: [self._row(i)[name] for i in indices] for name in columns}


def _interactor(**kwargs):
    source = _FakeSource(**kwargs)
    return source, QueryTraceInteractor(
        source=source, events=DeriveTraceEventsInteractor()
    )


# ---- 1: 何を返すか ----

class TestTheAnalysisReturnsTheDeclaredColumnsForTheWindow:
    def test_the_window_selects_the_rows(self):
        # Arrange
        source, query = _interactor(rows=100)

        # Act
        got = query.analyse(_JOB, start=_T0 + 10 * _STEP, end=_T0 + 20 * _STEP)

        # Assert
        assert got.rows == 10
        assert got.columns["time"] == [_T0 + i * _STEP for i in range(10, 20)]
        assert got.window == (_T0 + 10 * _STEP, _T0 + 20 * _STEP)

    def test_the_returned_columns_are_exactly_the_declaration(self):
        """列の一覧を front と検定へ書き写さない（宣言からの導出）。"""
        # Arrange
        source, query = _interactor(rows=20)

        # Act
        got = query.analyse(_JOB)

        # Assert
        assert tuple(got.columns) == ANALYSIS_COLUMNS
        assert source.read_calls[0]["columns"] == ANALYSIS_COLUMNS

    def test_the_declaration_covers_what_the_event_derivation_needs(self):
        """読む列 ⊇ 使う列（事象導出が要る列を取り落とすと 0 件で黙って成功する）。"""
        # Arrange
        from simulator.sim_ui.usecase.derive_trace_events import REQUIRED_COLUMNS

        # Act / Assert
        assert set(REQUIRED_COLUMNS) <= set(ANALYSIS_COLUMNS)

    def test_the_events_are_derived_from_the_same_read(self):
        # Arrange
        source, query = _interactor(rows=100)

        # Act
        got = query.analyse(_JOB)

        # Assert: 1 回の読みから列も事象も出る（事象のために読み直さない）。
        assert len(source.read_calls) == 1
        kinds = {e.kind for e in got.events}
        assert {HALT, POSITION_OPENED, MARGIN_FLOOR_BREACH} <= kinds, kinds

    def test_the_margin_floor_comes_from_the_run_not_from_a_default(self):
        """閾値は run の設定（stop-out 水準）であり、usecase が発明しない。"""
        # Arrange: 閾値の無い run。
        source, query = _interactor(rows=100, floor=None)

        # Act
        got = query.analyse(_JOB)

        # Assert
        assert all(e.kind != MARGIN_FLOOR_BREACH for e in got.events)
        # 正の対照: 他の事象は出ている（「事象が 1 件も出ない」実装ではない）。
        assert got.events

    def test_the_drawdown_is_computed_from_the_equity_column(self):
        # Arrange
        source, query = _interactor(rows=100, deposit=10_000.0)

        # Act
        got = query.analyse(_JOB)

        # Assert: equity は 10,000 から最大 60 下がる（`_row` の作り）。
        assert got.drawdown["equity_dd_maximal"] == pytest.approx(60.0)
        assert got.drawdown["equity_dd_absolute"] == pytest.approx(60.0)
        assert got.drawdown["equity_dd_maximal_percent"] == pytest.approx(0.6)

    def test_the_drawdown_formula_is_borrowed_not_rewritten(self):
        """`usecase/mt5_parity` の実体をそのまま使う（第 2 の DD 式を作らない）。"""
        # Arrange
        from simulator.usecase.mt5_parity import equity_dd_maximal

        source, query = _interactor(rows=100)

        # Act
        got = query.analyse(_JOB)

        # Assert
        assert got.drawdown["equity_dd_maximal"] == equity_dd_maximal(
            got.columns["equity"], 10_000.0
        )

    def test_an_empty_window_returns_zero_rows_without_failing(self):
        """「その期間に評価点が無かった」は誤りではない（値で読めるようにする）。"""
        # Arrange
        source, query = _interactor(rows=10)

        # Act
        got = query.analyse(_JOB, start=_T0 + 10_000 * _STEP, end=_T0 + 10_001 * _STEP)

        # Assert
        assert got.rows == 0
        assert got.events == ()
        assert got.drawdown["equity_dd_maximal"] == 0.0


class TestTheExtentIsExposedSoTheFrontCanChooseAWindow:
    def test_it_reports_the_row_count_and_the_bounds(self):
        # Arrange
        source, query = _interactor(rows=250)

        # Act
        got = query.extent(_JOB)

        # Assert
        assert (got.rows, got.first_time) == (250, _T0)
        assert got.last_time == _T0 + 249 * _STEP
        # 範囲の問い合わせで行を読まない。
        assert source.read_calls == []


# ---- 2: 返す量の上限（読む前に断る） ----

class TestTheWindowIsRefusedBeforeAnythingIsRead:
    def test_a_window_wider_than_the_cap_is_refused(self):
        # Arrange
        source, query = _interactor(rows=MAX_RETURNED_ROWS + 1)

        # Act / Assert
        with pytest.raises(TraceWindowTooWideError):
            query.analyse(_JOB)

    def test_nothing_is_read_when_the_window_is_refused(self):
        """**計算量テスト**: 読んでから断るのは「作ってから捨てる」である。"""
        # Arrange
        source, query = _interactor(rows=MAX_RETURNED_ROWS + 1)

        # Act
        with pytest.raises(TraceWindowTooWideError):
            query.analyse(_JOB)

        # Assert: 行の読みは 1 回も発行されていない。
        assert source.read_calls == [], source.read_calls
        # 正の対照: 断るための問い合わせ（件数）は発行されている。
        assert source.count_calls, "件数の問い合わせすら発行していない"

    def test_the_message_names_the_counts_so_the_caller_can_narrow_the_window(self):
        # Arrange
        source, query = _interactor(rows=MAX_RETURNED_ROWS + 5)

        # Act
        with pytest.raises(TraceWindowTooWideError) as caught:
            query.analyse(_JOB)

        # Assert: 「多すぎる」だけでは窓をどれだけ狭めればよいか分からない。
        message = str(caught.value)
        assert str(MAX_RETURNED_ROWS + 5) in message
        assert str(MAX_RETURNED_ROWS) in message

    def test_a_window_at_the_cap_is_accepted(self):
        """境界の向きを固定する（上限ちょうどは通る）。"""
        # Arrange
        source, query = _interactor(rows=MAX_RETURNED_ROWS)

        # Act
        got = query.analyse(_JOB)

        # Assert
        assert got.rows == MAX_RETURNED_ROWS

    def test_the_count_is_asked_for_the_same_window_that_would_be_read(self):
        """件数の窓と読む窓が食い違えば、上限判定は別の窓を測っていることになる。"""
        # Arrange
        source, query = _interactor(rows=100)
        start, end = _T0 + 5 * _STEP, _T0 + 15 * _STEP

        # Act
        query.analyse(_JOB, start=start, end=end)

        # Assert
        assert source.count_calls[-1] == {"start": start, "end": end}
        read = source.read_calls[-1]
        assert (read["start"], read["end"]) == (start, end)


class TestNothingIsReadTwice:
    """**計算量テスト**: 読んだ行数 − 返した行数 = 0（usecase 段）。"""

    def test_the_rows_are_read_exactly_once_per_analysis(self):
        # Arrange
        source, query = _interactor(rows=100)

        # Act
        got = query.analyse(_JOB, start=_T0 + 10 * _STEP, end=_T0 + 30 * _STEP)

        # Assert
        assert len(source.read_calls) == 1
        assert got.rows == 20
        # 正の対照: 0 行の一致を見ていない。
        assert got.rows > 0

    @pytest.mark.parametrize("total_rows", [200, 2_000])
    def test_the_returned_amount_is_set_by_the_window_not_the_run_length(
        self, total_rows
    ):
        """窓を固定したまま run 長を変えても返す量が増えない（オーダーの表明）。"""
        # Arrange
        source, query = _interactor(rows=total_rows)

        # Act
        got = query.analyse(_JOB, start=_T0 + 10 * _STEP, end=_T0 + 20 * _STEP)

        # Assert
        assert got.rows == 10
        assert len(source.read_calls) == 1

    def test_two_run_lengths_agree_on_the_returned_amount(self):
        # Arrange / Act
        observed = []
        for total in (200, 2_000):
            source, query = _interactor(rows=total)
            got = query.analyse(_JOB, start=_T0 + 10 * _STEP, end=_T0 + 20 * _STEP)
            observed.append((got.rows, len(source.read_calls)))

        # Assert
        assert observed[0] == observed[1], observed
        assert observed[0][0] > 0


# ---- 3: 窓の指定の妥当性 ----

class TestAnInvalidWindowFailsLoudly:
    def test_a_reversed_window_is_refused(self):
        # Arrange
        source, query = _interactor(rows=100)

        # Act / Assert: 0 行で成功させると「窓を間違えたのに成功」になる。
        with pytest.raises(ValueError):
            query.analyse(_JOB, start=_T0 + 10 * _STEP, end=_T0)
        assert source.read_calls == []


# ---- 4: どの窓なら収まるかをサーバが答える（実測で見つかった欠陥の是正） ----

class TestTheServerAnswersWhichWindowFits:
    """front が窓の幅を**推測しない**。

    なぜ（実測・2026-09-10。実ティック 1 ヶ月 run で再現した）:
        front が「上限 ÷ 全行数」の比で時間幅を決める形にしていたが、ティック密度は
        一様ではない（週末・立会時間）。実 run（1,036,394 行）でその比から出した窓には
        **59,030 行**が入り、初回表示が 413 になった。比例配分は「密度が一様」という
        **偽の前提**に立った推測である。

        上限に収まる窓を知っているのはサーバ（行の分布を持つ側）であり、件数の
        問い合わせは行を 1 つも materialise しない。推測をやめて測る。

    安全率を上げる（0.5 → 0.1 等）のは対症療法である——密度の偏りは残り、別の run で
    また外れる。原因（推測していること）を除去する。
    """

    def test_the_extent_carries_a_window_that_fits_under_the_cap(self):
        # Arrange: 全区間が上限を超える run。
        source, query = _interactor(rows=MAX_RETURNED_ROWS * 5)

        # Act
        extent = query.extent(_JOB)

        # Assert
        start, end = extent.suggested_window
        assert start is not None and end is not None
        assert source.count(_JOB, start=start, end=end) <= MAX_RETURNED_ROWS

    def test_the_suggested_window_is_not_absurdly_small(self):
        """収まりさえすればよいのではない（1 行の窓を返せば恒真になる）。"""
        # Arrange
        source, query = _interactor(rows=MAX_RETURNED_ROWS * 5)

        # Act
        start, end = query.extent(_JOB).suggested_window

        # Assert: 上限の半分以上を使う（画面が読める量を返す）。
        rows = source.count(_JOB, start=start, end=end)
        assert rows >= MAX_RETURNED_ROWS // 2, rows

    def test_it_covers_the_tail_of_the_run(self):
        """直近を返す（halt・維持率割れは run の終盤に集まる）。"""
        # Arrange
        source, query = _interactor(rows=MAX_RETURNED_ROWS * 5)

        # Act
        extent = query.extent(_JOB)

        # Assert: 上端は記録の最後の点を含む（半開なので +1 以上）。
        assert extent.suggested_window[1] > extent.last_time

    def test_a_run_that_fits_is_suggested_whole(self):
        # Arrange
        source, query = _interactor(rows=100)

        # Act
        extent = query.extent(_JOB)

        # Assert: 絞る理由が無いなら全区間。
        assert extent.suggested_window == (extent.first_time, extent.last_time + 1)

    def test_an_empty_run_suggests_no_window(self):
        # Arrange
        source, query = _interactor(rows=0)

        # Act / Assert: 記録が無いのに窓を発明しない。
        assert query.extent(_JOB).suggested_window == (None, None)

    def test_finding_the_window_reads_no_row(self):
        """**計算量テスト**: 窓を決めるために行を 1 つも読まない。"""
        # Arrange
        source, query = _interactor(rows=MAX_RETURNED_ROWS * 5)

        # Act
        query.extent(_JOB)

        # Assert
        assert source.read_calls == [], source.read_calls
        # 正の対照: 件数の問い合わせは発行されている（測っていないだけではない）。
        assert source.count_calls, "件数の問い合わせが 0 件"

    @pytest.mark.parametrize("total_rows", [MAX_RETURNED_ROWS * 5, MAX_RETURNED_ROWS * 50])
    def test_the_search_cost_does_not_grow_with_the_run_length(self, total_rows):
        """**計算量テスト**: run が 10 倍でも件数の問い合わせは対数的にしか増えない。

        件数そのものを期待値へ焼き込まない（焼き込むと浪費が仕様へ昇格する）。
        固定するのは「行数に比例して増えないこと」だけである。
        """
        # Arrange
        source, query = _interactor(rows=total_rows)

        # Act
        query.extent(_JOB)

        # Assert: 二分探索の上限（実装が持つ回数の上限）を超えない。
        from simulator.sim_ui.usecase.query_trace import MAX_WINDOW_PROBES

        assert len(source.count_calls) <= MAX_WINDOW_PROBES
        assert source.read_calls == []

    def test_two_run_lengths_agree_on_the_probe_budget(self):
        # Arrange / Act
        probes = []
        for total in (MAX_RETURNED_ROWS * 5, MAX_RETURNED_ROWS * 50):
            source, query = _interactor(rows=total)
            query.extent(_JOB)
            probes.append(len(source.count_calls))

        # Assert: 10 倍でも問い合わせ回数は数回しか増えない（対数）。
        assert probes[1] - probes[0] <= 5, probes
        assert probes[0] > 0


# ---- 5: 宣言の食い違いは `-O` でも落ちる（工程 5 レビュー 🟡-1） ----

class TestTheDeclarationConsistencyIsEnforcedEvenUnderO:
    """module 直下の `assert` は `python -O` で**消える**。

    実測（2026-09-11）:
        失敗する module 直下 assert を持つモジュールを import すると、通常は
        `AssertionError` が出るが `-O` では**何も起きない**（bytecode から消える）。

    なぜそれが害か:
        この表明は「事象導出が要る列が、返す列の宣言に含まれている」ことを起動時に
        固定している。`-O` で消えると、宣言が食い違ったまま起動し、実行時に
        `KeyError` になる——それは TraceApiController._guarded の翻訳表に無いので
        **500 になり、front は理由を受け取れない**。

        同一変更集合の中で判断が割れていた: `simulator/domain/bar_time.py` は同じ
        「宣言の食い違い」に対して例外送出（`ConfigError`）を選んでいる。揃える。
    """

    def test_the_module_has_no_top_level_assert(self):
        # Arrange
        import ast
        import pathlib

        import simulator.sim_ui.usecase.query_trace as mod

        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))

        # Act
        top_level_asserts = [n for n in tree.body if isinstance(n, ast.Assert)]

        # Assert: `-O` で消える表明を起動時ゲートに使わない。
        assert top_level_asserts == [], [n.lineno for n in top_level_asserts]

    def test_a_diverging_declaration_raises_a_config_error(self):
        # Arrange
        from simulator.domain.exceptions import ConfigError
        from simulator.sim_ui.usecase.query_trace import verify_column_declarations

        # Act / Assert: 要る列が返す列に含まれていない組み合わせ。
        with pytest.raises(ConfigError) as caught:
            verify_column_declarations(
                returned=("time", "equity"), required=("time", "margin_level")
            )
        assert "margin_level" in str(caught.value)

    def test_the_real_declarations_pass(self):
        """正の対照: 何でも例外にしているわけではない（現行の宣言は整合している）。

        「例外が出ないこと」だけに頼らない——何も表明しない検定は、関数が空になっても
        緑のままである。整合そのものと、検査が値を返さないことを明示的に表明する。
        """
        # Arrange
        from simulator.sim_ui.usecase.derive_trace_events import REQUIRED_COLUMNS
        from simulator.sim_ui.usecase.query_trace import verify_column_declarations

        # Act
        result = verify_column_declarations(
            returned=ANALYSIS_COLUMNS, required=REQUIRED_COLUMNS
        )

        # Assert
        assert result is None
        assert set(REQUIRED_COLUMNS) <= set(ANALYSIS_COLUMNS)

    def test_the_check_runs_at_import_time(self):
        """起動時に 1 回走ること（構文木で呼出を確かめる）。

        関数を定義しただけで呼ばなければ、食い違いは実行時まで見つからない。
        """
        # Arrange
        import ast
        import pathlib

        import simulator.sim_ui.usecase.query_trace as mod

        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))

        # Act: module 直下の式文としての呼出を探す。
        calls = [
            node.value.func.id
            for node in tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
        ]

        # Assert
        assert "verify_column_declarations" in calls, calls
