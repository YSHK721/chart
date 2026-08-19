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
from simulator.framework.tester_settings.loader import tester_settings_from_mapping
from simulator.tests.tester_settings_engine_fixtures import runnable_expert_mapping

# 実行可能な `[Tester]` マッピング（保証境界の内側）は既存の組み立て器が単一ソース。
# ここで `.ini` の値を書き写さない。
_EA_NAME = build_ea_subject_port().stem_of(runnable_expert_mapping()["Expert"])
_CATALOG = FakeSeriesCatalog({_EA_NAME: frozenset({"close"})})

#: `.ini` が指す銘柄（生トークン）と、それと食い違う銘柄。
_TESTER_SYMBOL = runnable_expert_mapping()["Symbol"]
_OTHER_SYMBOL = f"{_TESTER_SYMBOL}_OTHER"


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


def _submission(
    *, tester=None, inputs=(), ea_name: str = _EA_NAME, symbol=None
) -> JobSubmission:
    """既定は `.ini` と実行仕様が同じ実行対象を指す本文（本番 front と同じ形）。

    `symbol` を明示すると実行仕様側だけを差し替えられる（実行対象の不一致を作る）。
    既定値は `.ini` の `Symbol` から引く（銘柄名をここに書き写さない＝単一ソース）。
    """
    tester_map = dict(runnable_expert_mapping() if tester is None else tester)
    return JobSubmission(
        backtest={
            "ea_name": ea_name,
            "symbol": tester_map.get("Symbol", "") if symbol is None else symbol,
        },
        settings={"tester": tester_map, "inputs": list(inputs)},
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


# --- 2b. Symbol と backtest.symbol の一致（ISSUE-422・段階 2 §19.5） ----------

def test_Symbolとbacktest_symbolの不一致は受付で拒否される_Model1() -> None:
    """`Model=1`（写像層の防壁が生きる経路）でも**受付で**止める。

    現行は受付を素通りし、実行段で failed になる（遅い失敗）。同じ不一致が
    Modelling によって別経路で報告される非対称を、受付側へ寄せて解消する。
    """
    # Arrange
    sut = _interactor()
    sub = _submission(tester=runnable_expert_mapping(Model="1"), symbol=_OTHER_SYMBOL)
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError) as caught:
        sut.execute(sub)
    message = str(caught.value)
    assert _TESTER_SYMBOL in message, f"`.ini` の銘柄名が文言に無い: {message}"
    assert _OTHER_SYMBOL in message, f"実行仕様の銘柄名が文言に無い: {message}"


def test_Symbolとbacktest_symbolの不一致は受付で拒否される_math() -> None:
    """`Model=3`（Math calculations）は写像層に `.ini` の Symbol が到達しない。

    `TesterSettings.effective()` は `MATH_CALCULATIONS` のとき `INERT_FIELDS`
    （`symbol` を含む）を None 化するため、写像層の `_require_match` は不一致を
    黙認する（ISSUE-422）。受付で止めなければ「指定と違う銘柄の結果」が静かに出る。
    """
    # Arrange
    sut = _interactor()
    sub = _submission(tester=runnable_expert_mapping(Model="3"), symbol=_OTHER_SYMBOL)
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError) as caught:
        sut.execute(sub)
    assert _OTHER_SYMBOL in str(caught.value)


def test_銘柄不一致の投入は台帳へ書かず子も起こさない() -> None:
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _submission(symbol=_OTHER_SYMBOL)
    # Act
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)
    # Assert
    assert launcher.launched == []


def test_backtest_symbolが空文字なら不一致として拒否される() -> None:
    """実行仕様の銘柄が空文字の本文は実行対象が定まらない（`.ini` の Symbol との不一致）。

    真の**不在**（`symbol` キーそのものが無い）はここには届かない——本番の
    `required_backtest_keys` は `symbol` を必須に含むため、先行する必須キー検査が
    先に 400 を返す（本検定は `no_required_backtest_keys` を使うため空文字で代替する）。
    """
    # Arrange
    sut = _interactor()
    sub = _submission(symbol="")
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


def test_大小文字と空白の違いは同一銘柄と見なされない() -> None:
    """比較は**生トークンの恒等**である（正規化しない）。

    `.ini` の `Symbol` は MT5 が解決する銘柄名そのものであり、大小文字や前後空白を
    こちらで吸収すると「受付は通ったが実行対象は別」の隙間ができる。正規化比較
    （`strip().upper()` 等）へ書き換えると本検定が落ちる。
    """
    # Arrange: 実行仕様側だけを小文字化＋前後空白づけ
    sut = _interactor()
    sub = _submission(symbol=f" {_TESTER_SYMBOL.lower()} ")
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError) as caught:
        sut.execute(sub)
    assert _TESTER_SYMBOL.lower() in str(caught.value)


def test_受付が比較する生トークンは設定モデルの値と恒等である() -> None:
    """受付の比較対象（生トークン）が設定モデルの `symbol` と同じ値であること。

    受付は `.ini` の生トークンを直接比べる。もし正規化を挟むと「受付は通るが
    実行対象は別」の隙間ができる。恒等であることをここで固定する。
    """
    # Arrange / Act
    settings = tester_settings_from_mapping(runnable_expert_mapping(), [])
    # Assert
    assert settings.symbol == _TESTER_SYMBOL


def test_math実行では実行時ビューに銘柄が到達しない() -> None:
    """受付層で判定する決定的根拠（§19.5）を機械で固定する。

    `Model=3`（Math calculations）では `effective()` が `symbol` を None 化するため、
    写像層は `.ini` の銘柄を見られない＝写像層では不一致を検出できない。
    """
    # Arrange / Act
    effective = tester_settings_from_mapping(runnable_expert_mapping(Model="3"), []).effective()
    # Assert
    assert effective.symbol is None


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
