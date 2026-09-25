"""`trace` ブロックの受付（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.4/§6.6）。

固定する仕様:

    1. 投入本文は `"trace": {"enabled": true, "start": <epoch 秒>, "end": <epoch 秒>}`。
       sizing / strategy / settings と同じ「不在・空なら OFF＝既存挙動 byte 等価」。
    2. **時刻は epoch 秒（JSON 整数）で受ける**（是正 F-1/F-2）。整数なので
       `submit_job` が**受付時に**検査できる（framework loader への依存なし）。
       「投入は通ったが実行だけ落ちる」を作らない（`submit_job.py:89-91` の既存規律）。
    3. `start > end` は受付で拒否する（ConfigError（`simulator/domain/exceptions.py`） 相当を受付例外へ翻訳する）。
       黙って空窓にすると「窓を間違えたのに 0 行で成功する」run ができる。
    4. 窓の判定規則そのものは受付層に**書かない**。規則の実体は `TraceWindow`
       （adapter）が唯一持ち、usecase は Composition Root が注入した Callable を
       呼ぶだけである——usecase は adapter を import できない（層ゲート）ので、
       規則を写す以外の道は注入しかない。既存の allowed_backtest_keys /
       required_series / `settings_validator` と同一の様式であり、新しい抽象では
       ない（§6.5.3 の YAGNI に触れない）。

**恒真にしない**: 「拒否されること」だけを測ると、何を投入しても拒否する実装が緑になる。
    正の対照（正しい窓は受理されて台帳へ届くこと）を必ず対で置く。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from simulator.sim_ui.usecase.job_models import (
    JobSubmission,
    JobSubmissionInvalidError,
)
from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor

_EPOCH = 1_704_067_200
_BACKTEST = {"ea_name": "TC24051901", "symbol": "EURUSD"}


class _Ledger:
    def __init__(self):
        self.created = []

    def create(self, submission):
        from simulator.sim_ui.domain.simulation_job import SimulationJob

        self.created.append(submission)
        return SimulationJob.received("0" * 32)

    def load(self, job_id):
        return None

    def update(self, job, *, expect):
        return None


class _Launcher:
    def __init__(self):
        self.launched = []

    def launch(self, job_id):
        self.launched.append(job_id)


class _CountingWindowCheck:
    """注入される窓検査（実体は `TraceWindow.of`）の Spy。

    規則そのものは本体（adapter）が持ち、ここでは「呼ばれたか」「何を渡されたか」
    だけを見る。規則を検定側で書き直すと、それ自体が第 2 実装になる。
    """

    def __init__(self):
        from simulator.adapter.trace.trace_window import TraceWindow

        self._of = TraceWindow.of
        self.calls: "list[tuple]" = []

    def __call__(self, start, end):
        self.calls.append((start, end))
        return self._of(start, end)


def _interactor(ledger=None, launcher=None, window_check=None):
    return SubmitJobInteractor(
        ledger=ledger if ledger is not None else _Ledger(),
        launcher=launcher if launcher is not None else _Launcher(),
        series_catalog=None,
        required_series=lambda ea_name: "close",
        stop_loss_catalog=None,
        allowed_backtest_keys=lambda: frozenset(_BACKTEST),
        required_backtest_keys=lambda: frozenset(),
        trace_window_check=(
            window_check if window_check is not None else _CountingWindowCheck()
        ),
    )


def _submit(trace, window_check=None):
    ledger = _Ledger()
    _interactor(ledger=ledger, window_check=window_check).execute(
        JobSubmission(backtest=dict(_BACKTEST), trace=trace)
    )
    return ledger


# ---- 1: DTO の形 ----

class TestTheSubmissionCarriesATraceBlock:
    def test_the_dataclass_declares_the_block_with_an_off_default(self):
        # Arrange / Act
        submission = JobSubmission(backtest=dict(_BACKTEST))

        # Assert: 不在は None（既定 OFF・既存挙動 byte 等価）。
        assert submission.trace is None

    @pytest.mark.parametrize(
        "block,enabled",
        [
            (None, False),
            ({}, False),
            ({"enabled": False}, False),
            ({"enabled": True}, True),
            ({"enabled": True, "start": _EPOCH, "end": _EPOCH + 60}, True),
        ],
        ids=["absent", "empty", "off", "on", "on_with_window"],
    )
    def test_the_enabled_flag_follows_the_same_shape_as_sizing(self, block, enabled):
        """sizing と同じ「不在・空・`enabled` 偽なら OFF」。"""
        # Arrange / Act
        submission = JobSubmission(backtest=dict(_BACKTEST), trace=block)

        # Assert
        assert submission.trace_enabled is enabled

    def test_the_controller_reads_the_block_from_the_body(self):
        # Arrange
        import json

        from simulator.sim_ui.adapter.job_api_controller import JobApiController

        captured = {}

        class _Submit:
            def execute(self, submission):
                from simulator.sim_ui.usecase.job_models import JobView

                captured["trace"] = submission.trace
                return JobView(job_id="0" * 32, status="running")

        controller = JobApiController(
            submit=_Submit(), query=None, cancel=None, fetch_result=None
        )
        body = {
            "backtest": dict(_BACKTEST),
            "trace": {"enabled": True, "start": _EPOCH, "end": _EPOCH + 60},
        }

        # Act
        response = controller.submit(json.dumps(body).encode("utf-8"))

        # Assert
        assert response.status == 202
        assert captured["trace"] == body["trace"]


# ---- 2・3: 受付時の窓検査 ----

class TestTheWindowIsCheckedAtReception:
    """整数で受けるからこそ受付段で検査できる（是正 F-1/F-2）。"""

    def test_a_valid_window_is_accepted_and_reaches_the_ledger(self):
        """正の対照。これが無いと「何を出しても拒否」で全表明が緑になる。"""
        # Arrange / Act
        ledger = _submit({"enabled": True, "start": _EPOCH, "end": _EPOCH + 3600})

        # Assert
        assert len(ledger.created) == 1
        assert ledger.created[0].trace == {
            "enabled": True, "start": _EPOCH, "end": _EPOCH + 3600
        }

    def test_an_unwindowed_trace_is_accepted(self):
        """窓未指定（全区間）も正当な指定であること（境界値）。"""
        # Arrange / Act
        ledger = _submit({"enabled": True})

        # Assert
        assert len(ledger.created) == 1

    def test_an_empty_but_valid_window_is_accepted(self):
        """`start == end` は「空だが正しい指定」（境界値）。"""
        # Arrange / Act
        ledger = _submit({"enabled": True, "start": _EPOCH, "end": _EPOCH})

        # Assert
        assert len(ledger.created) == 1

    def test_an_inverted_window_is_refused_before_the_ledger_is_touched(self):
        # Arrange
        ledger = _Ledger()
        launcher = _Launcher()

        # Act / Assert
        with pytest.raises(JobSubmissionInvalidError) as excinfo:
            _interactor(ledger=ledger, launcher=launcher).execute(
                JobSubmission(
                    backtest=dict(_BACKTEST),
                    trace={"enabled": True, "start": _EPOCH + 60, "end": _EPOCH},
                )
            )

        # 判定前に台帳へ書かない・子プロセスを起こさない（拒否したジョブの残骸を作らない）。
        assert ledger.created == []
        assert launcher.launched == []
        # 文言は 2 つを必ず運ぶ: どのブロックの誤りか（trace）と、根本原因
        # （窓の逆転）。どちらか一方では、運用者が「何を直せばよいか」に到達できない。
        message = str(excinfo.value)
        assert "trace" in message, message
        assert "トレース期間の開始が終了より後です" in message, message

    def test_a_one_sided_window_is_refused(self):
        """片側だけの指定は既定値で黙って埋めない。"""
        # Arrange / Act / Assert
        for block in (
            {"enabled": True, "start": _EPOCH},
            {"enabled": True, "end": _EPOCH},
        ):
            with pytest.raises(JobSubmissionInvalidError):
                _submit(block)

    def test_a_non_integer_bound_is_refused(self):
        """文字列日付を受け付けない（§6.4 の禁止事項）。

        受付側で strptime / fromisoformat を新しく書くと、同じ文字列が経路で違う
        時刻に化ける（ISSUE-401 で 32,400 秒差を実測済みの同型）。日付 → epoch 秒の
        変換は front の責務である。
        """
        # Arrange / Act / Assert
        with pytest.raises(JobSubmissionInvalidError):
            _submit({"enabled": True, "start": "2024-01-01", "end": "2024-01-02"})

    def test_a_disabled_trace_block_is_not_validated(self):
        """OFF のブロックは検査しない（既存挙動 byte 等価の維持）。"""
        # Arrange / Act: 窓が壊れていても OFF なら通る。
        ledger = _submit({"enabled": False, "start": _EPOCH + 60, "end": _EPOCH})

        # Assert
        assert len(ledger.created) == 1


# ---- 4: 判定規則を受付層へ書き写さない ----

class TestTheReceptionDoesNotReimplementTheWindowRule:
    def test_the_reception_asks_the_injected_rule_instead_of_deciding_itself(self):
        """受付検証が注入された窓検査を**実際に呼ぶ**こと。

        呼ばずに自前で判定していると、規則が受付層と `TraceWindow` の 2 箇所に
        存在することになる（片方だけ直る形の食い違いが例外を出さずに起きる）。
        """
        # Arrange
        check = _CountingWindowCheck()

        # Act
        _submit({"enabled": True, "start": _EPOCH, "end": _EPOCH + 60}, window_check=check)

        # Assert: 投入された境界そのものが渡っている。
        assert check.calls == [(_EPOCH, _EPOCH + 60)]

    def test_the_rule_is_not_consulted_when_the_trace_is_off(self):
        """計算量: OFF の投入では窓検査を **1 回も発行しない**。

        「作ってから捨てる」形（OFF なのに毎回窓を組んで破棄する）の不在を固定する。
        """
        # Arrange
        check = _CountingWindowCheck()

        # Act
        _submit(None, window_check=check)
        _submit({}, window_check=check)
        _submit({"enabled": False}, window_check=check)

        # Assert: 発行 0。
        assert check.calls == []
        # 正の対照: 同じ Spy が ON では動くこと（構造的な恒真でないことの実証）。
        _submit({"enabled": True, "start": _EPOCH, "end": _EPOCH}, window_check=check)
        assert len(check.calls) == 1

    def test_the_module_contains_no_second_date_rule(self):
        """日付解釈の第 2 実装が受付層へ持ち込まれていないこと（§6.4 の禁止事項）。"""
        # Arrange
        import simulator.sim_ui.usecase.submit_job as mod

        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))

        # Act
        attributes = {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        } | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

        # Assert
        assert attributes & {"strptime", "fromisoformat", "strftime"} == set(), attributes
        # 正の対照: 走査が空振りしていない。
        assert "JobSubmissionInvalidError" in attributes, sorted(attributes)[:20]

    def test_the_usecase_layer_still_imports_no_adapter_at_module_level(self):
        """usecase → adapter の外向き依存を module 直下に作らないこと（層ゲート）。"""
        # Arrange
        import simulator.sim_ui.usecase.submit_job as mod

        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))

        # Act: module 直下の import だけを見る。
        # 分岐ではなく内包表記で集める（検出範囲は同一・実行経路が入力で変わらない）。
        top_level = {
            alias.name
            for n in tree.body if isinstance(n, ast.Import) for alias in n.names
        } | {
            n.module for n in tree.body if isinstance(n, ast.ImportFrom) and n.module
        }

        # Assert
        offenders = {m for m in top_level if ".adapter" in m or m.startswith("simulator.main")}
        assert offenders == set(), offenders
        assert top_level, "import が 1 件も読めていない（走査が空振り）"
