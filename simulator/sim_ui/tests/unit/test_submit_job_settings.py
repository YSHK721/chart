"""settings ブロック（Phase 8 スライス 3）の受付検証の検定。

固定する不変条件（設計 §18.4 スライス 3 の通過条件）:
    1. 規則違反（B〜Q）は**受付で**拒否し、文言に `rule_id` を載せる（沈黙で実行段まで
       運ばない）。検証の実体は `framework/tester_settings/loader` の単一ソースであり、
       第 2 実装を作らない。
    2. `Expert` の語幹（`ea_stem`）と `backtest.ea_name` の不一致は拒否する。
       食い違ったまま実行すると「指定した EA と違う EA の結果」が静かに出る。
    3. T-2 裁定: `[TesterInputs]`（`inputs`）は Phase 8 では実行不能（`EA_INPUT_BINDINGS`
       が空＝必ず `ConfigError`）。非空なら受付で拒否し、理由を文言に明記する。
    4. settings 不在の投入は**現行と同一**（検証を巻き込まない・OFF 等価）。

Port の束縛は**本番の合成根から取る**（検定側でフェイクを作ると、束縛が変わっても
テストが緑のままになる＝「テストは緑だが本番は拒否する」を作る）。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.domain.simulation_job import JobStatus
from simulator.sim_ui.main.composition_root_jobs import (
    build_ea_subject_port,
    build_settings_validation_port,
)
from simulator.sim_ui.tests.integration._fake_ports import (
    FakeLauncher,
    FakeLedger,
    FakeSeriesCatalog,
    FakeStopLossCatalog,
    allowed_backtest_keys,
    no_required_backtest_keys,
    required_series,
)
from simulator.sim_ui.usecase.job_models import JobSubmission, JobSubmissionInvalidError
from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor
from simulator.tests.tester_settings_engine_fixtures import runnable_expert_mapping

# 実行可能な `[Tester]` マッピング（保証境界の内側）は既存の組み立て器が単一ソース。
# ここで `.ini` の値を書き写さない。
_EA_NAME = build_ea_subject_port().stem_of(runnable_expert_mapping()["Expert"])
_CATALOG = FakeSeriesCatalog({_EA_NAME: frozenset({"close"})})


def _interactor(launcher=None) -> SubmitJobInteractor:
    return SubmitJobInteractor(
        ledger=FakeLedger(),
        launcher=launcher or FakeLauncher(),
        series_catalog=_CATALOG,
        required_series=required_series,
        stop_loss_catalog=FakeStopLossCatalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
        settings_validator=build_settings_validation_port(),
        ea_subject=build_ea_subject_port(),
    )


def _submission(*, tester=None, inputs=(), ea_name: str = _EA_NAME) -> JobSubmission:
    return JobSubmission(
        backtest={"ea_name": ea_name},
        settings={
            "tester": dict(runnable_expert_mapping() if tester is None else tester),
            "inputs": list(inputs),
        },
    )


# --- 1. 規則違反は rule_id つきで拒否 ---------------------------------------

def test_未知の時間足ラベルは受付で拒否される() -> None:
    # Arrange: Period=M7 は規則 O（未知の時間足ラベル）に触れる
    sut = _interactor()
    sub = _submission(tester=runnable_expert_mapping(Period="M7"))
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError) as caught:
        sut.execute(sub)
    message = str(caught.value)
    assert "Period" in message
    assert "M7" in message
    assert "O" in message, f"rule_id が文言に載っていない: {message}"


def test_規則違反の投入は台帳へ書かず子も起こさない() -> None:
    """拒否した投入の残骸を作らない（判定前に台帳へ書かない）。"""
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _submission(tester=runnable_expert_mapping(Period="M7"))
    # Act
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)
    # Assert
    assert launcher.launched == []


# --- 2. Expert の語幹と ea_name の一致 --------------------------------------

def test_Expertの語幹とea_nameの不一致は拒否される() -> None:
    # Arrange
    sut = _interactor()
    sub = _submission(ea_name="OtherEa")
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError) as caught:
        sut.execute(sub)
    assert "OtherEa" in str(caught.value)


def test_Expert不在のsettingsは不一致として拒否される() -> None:
    """`Expert` が無い設定（Indicator 用）は EA を特定できない＝実行対象が定まらない。"""
    # Arrange
    tester = runnable_expert_mapping()
    tester.pop("Expert")
    sut = _interactor()
    sub = _submission(tester=tester)
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


# --- 3. T-2: EA inputs は Phase 8 では実行不能 ------------------------------

def test_inputsが非空なら拒否される() -> None:
    # Arrange
    sut = _interactor()
    sub = _submission(inputs=("MAPeriod=3||2||1||22||Y",))
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError) as caught:
        sut.execute(sub)
    assert "TesterInputs" in str(caught.value)


# --- 4. 受理される settings ---------------------------------------------------

def test_保証境界内のsettingsは受理される() -> None:
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    # Act
    got = sut.execute(_submission())
    # Assert
    assert got.status == JobStatus.RUNNING.value
    assert len(launcher.launched) == 1


# --- 5. settings 不在＝現行と同一 --------------------------------------------

def test_settings不在の投入は検証を巻き込まない() -> None:
    """OFF 等価（検証 Port を呼ばない・現行受付と同じ結果）。"""
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    # Act
    got = sut.execute(JobSubmission(backtest={"ea_name": "AnyName"}))
    # Assert
    assert got.status == JobStatus.RUNNING.value
    assert len(launcher.launched) == 1
