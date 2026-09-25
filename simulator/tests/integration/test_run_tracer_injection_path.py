"""`run_tracer` の投入路（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.6.3・是正 D-2）。

固定する仕様:

    1. `build_interactor` に `run_tracer`（既定 `None`）が在り、`RunBacktestInteractor` の
       同名の拡張点へ**そのまま**渡る。既定 `None` は byte 等価（先例は session_calendar /
       position_manager / schedule）。
    2. **是正 D-2**: `run_tracer` は `sim_ui/main/composition_root_jobs._INJECTED_ONLY_KEYS`
       に在る。`allowed_backtest_keys()` は `inspect.signature(build_interactor)` の反射で
       あり、追加しないと **JSON から `backtest.run_tracer` を投入できてしまう**
       （JSON スカラーでは表現できない実体であり、受け取れば必ず実行段で壊れる）。
    3. `run_tracer` は**実体で**渡す（position_manager と同じ拡張点の扱い）。
       JSON スカラーでは渡せないものは extensions 経由である。

**恒真にしない**: 「集合に無いこと」だけを測ると、集合そのものが空でも緑になる。
    各表明に正の対照（集合が実在し、既知の要素を含むこと）を同梱する。
"""
from __future__ import annotations

import inspect

import pytest

from simulator.main import build_interactor
from simulator.sim_ui.main.composition_root_jobs import (
    _INJECTED_ONLY_KEYS,
    allowed_backtest_keys,
    required_backtest_keys,
)
from simulator.usecase.run_backtest import RunBacktestInteractor


# ---- 1: 拡張点が在り、そのまま渡る ----

class TestTheEngineExtensionPointIsReachableFromTheCompositionRoot:
    """`build_interactor(run_tracer=...)` → `RunBacktestInteractor(run_tracer=...)`。"""

    def test_the_signature_declares_the_extension_point_with_a_none_default(self):
        # Arrange / Act
        parameters = inspect.signature(build_interactor).parameters

        # Assert
        assert "run_tracer" in parameters
        assert parameters["run_tracer"].default is None
        assert parameters["run_tracer"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_the_interactor_accepts_the_same_keyword(self):
        """名前を写し替えないこと（`build_interactor` 側と engine 側で同名）。"""
        # Arrange / Act
        parameters = inspect.signature(RunBacktestInteractor.__init__).parameters

        # Assert
        assert "run_tracer" in parameters
        assert parameters["run_tracer"].default is None

    def test_the_extension_point_is_declared_last_like_the_other_injected_entities(self):
        """並びの末尾に足すこと（既存の並びを 1 文字も動かさない）。

        公開シグネチャは事実上の HTTP スキーマであり、既存の並びを崩すと
        `test_ea_bindings_are_declaration_driven.py` の厳密等価表明が壊れる。
        """
        # Arrange / Act
        names = tuple(inspect.signature(build_interactor).parameters)

        # Assert
        assert names[-1] == "run_tracer"
        assert names[-4:] == (
            "strategy_decorator", "strategy_override", "position_manager", "run_tracer",
        )


# ---- 2: 是正 D-2（JSON から投入させない）----

class TestTheTracerCannotBeInjectedThroughTheJobSpecification:
    """`run_tracer` が注入専用キーであること。"""

    def test_it_is_declared_injected_only(self):
        # Arrange / Act / Assert
        assert "run_tracer" in _INJECTED_ONLY_KEYS
        # 正の対照: 集合が空なら上は起こり得ない（既知の要素で実在を示す）。
        assert {"strategy_decorator", "strategy_override"} <= _INJECTED_ONLY_KEYS

    def test_the_allowed_backtest_keys_do_not_contain_it(self):
        # Arrange / Act
        allowed = allowed_backtest_keys()

        # Assert
        assert "run_tracer" not in allowed
        # 正の対照: 集合が空なら上は恒真になる。
        assert "ea_name" in allowed and "symbol" in allowed, sorted(allowed)

    def test_it_is_never_a_required_key(self):
        """既定 `None` である以上、必須集合へ紛れ込まないこと。"""
        # Arrange / Act / Assert
        assert "run_tracer" not in required_backtest_keys()
        assert required_backtest_keys(), "必須集合が空（検定が何も測っていない）"

    def test_a_submission_carrying_the_key_is_refused_at_reception(self):
        """受付段で弾かれること（「投入は通ったが実行だけ落ちる」を作らない）。"""
        # Arrange
        from simulator.sim_ui.usecase.job_models import (
            JobSubmission,
            JobSubmissionInvalidError,
        )
        from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor

        class _Ledger:
            def create(self, submission):
                raise AssertionError("受付検証を通過してはならない")

            def load(self, job_id):
                return None

            def update(self, job, *, expect):
                raise AssertionError("受付検証を通過してはならない")

        interactor = SubmitJobInteractor(
            ledger=_Ledger(),
            launcher=None,
            series_catalog=None,
            required_series=lambda ea_name: "close",
            stop_loss_catalog=None,
            allowed_backtest_keys=allowed_backtest_keys,
            required_backtest_keys=required_backtest_keys,
        )

        # Act / Assert
        with pytest.raises(JobSubmissionInvalidError) as excinfo:
            interactor.execute(JobSubmission(backtest={"run_tracer": {"enabled": True}}))
        assert "run_tracer" in str(excinfo.value)


# ---- 3: 実体がエンジンへ届く ----

class TestTheTracerReachesTheEngineAsAnEntity:
    """extensions 経由で渡した実体が _RunState.tracer に載ること。"""

    def test_the_injected_tracer_is_the_one_the_run_observes_with(self, tmp_path):
        # Arrange: 最小の合成 CSV で 1 run を組む（数値は本検定の関心ではない）。
        from simulator.usecase.run_trace_ports import RunTracePort

        class _Spy(RunTracePort):
            def __init__(self):
                self.calls = 0

            def observe(self, point, account, open_trades, halted):
                self.calls += 1

        spy = _Spy()
        csv = tmp_path / "bars.csv"
        csv.write_text(
            "time,open,high,low,close,volume,spread\n"
            + "".join(
                f"{1_704_067_200 + 60 * i},1.10,1.11,1.09,1.10{i % 10},1.0,0\n"
                for i in range(12)
            ),
            encoding="utf-8",
        )

        # Act
        controller, request = build_interactor(
            data_path=csv, symbol="EURUSD", period="M1", ea_name="TC24051901",
            initial_deposit=10_000.0, contract_size=1.0, volume_min=0.01,
            volume_max=100.0, volume_step=0.01, stops_level=0, digits=5,
            point_size=0.0001, leverage=100.0, ma_period=2, ma_method="sma",
            lot_size=1.0, stop_loss_points=500, take_profit_points=3000,
            run_tracer=spy,
        )
        controller.execute(request)

        # Assert: 注入した実体が実際に観測を受けた。
        assert spy.calls > 0

    def test_passing_no_tracer_is_the_same_as_passing_none(self, tmp_path):
        """既定経路の無改変（引数の不在＝`None` 明示と同一結果）。

        `controller._interactor` へは触らない——私有属性への到達は ISSUE-395/398 で
        是正済みのカプセル化破りである。ここは**結果**で測る（未注入の run が観測を
        1 回も発行しないことは `tests/unit/test_run_trace_observation.py` が持つ）。
        """
        # Arrange
        csv = tmp_path / "bars.csv"
        csv.write_text(
            "time,open,high,low,close,volume,spread\n"
            + "".join(
                f"{1_704_067_200 + 60 * i},1.10,1.11,1.09,1.10{i % 10},1.0,0\n"
                for i in range(12)
            ),
            encoding="utf-8",
        )
        common = dict(
            data_path=csv, symbol="EURUSD", period="M1", ea_name="TC24051901",
            initial_deposit=10_000.0, contract_size=1.0, volume_min=0.01,
            volume_max=100.0, volume_step=0.01, stops_level=0, digits=5,
            point_size=0.0001, leverage=100.0, ma_period=2, ma_method="sma",
            lot_size=1.0, stop_loss_points=500, take_profit_points=3000,
        )

        # Act
        absent_controller, absent_request = build_interactor(**common)
        absent = absent_controller.execute(absent_request)
        explicit_controller, explicit_request = build_interactor(
            **common, run_tracer=None
        )
        explicit = explicit_controller.execute(explicit_request)

        # Assert
        assert explicit.stats == absent.stats
        assert explicit.trades == absent.trades
        assert explicit.equity_curve == absent.equity_curve
        # 正の対照: 何も起きない run では上は恒真になる。
        assert absent.equity_curve, "評価点が 0 件（検定が何も測っていない）"
