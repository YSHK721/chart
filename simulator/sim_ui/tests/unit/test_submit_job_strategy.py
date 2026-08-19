"""SubmitJobInteractor の戦略項目 受付検証（P6-E5・E-3 カタログ再利用）の単体検定.

裁定（指示書 P6-E5）: 戦略条件が参照する indicator ⊆ 当該 ea_name の登録系列 を
**受付時に検証**する。満たさなければ明示拒否（無音で誤った実行をさせない）。省略時
（strategy 不在）は検証を巻き込まず既存挙動 byte 等価。実行時 fail-stop
（GenericConditionStrategy の IndicatorBufferError）は最後の砦。

**段階 2（§19.5）以降の位置づけ**: `strategy` ブロックを持つ投入は `execute` 冒頭の
受付ゲート（`_reject_strategy_block`）で拒否されるため、上記 P6-E5 の受付検証は
**到達不能**になった（検証コードは可逆性のため残置）。したがって本ファイルで
`execute` を呼ぶ検定はすべて段階 2 のゲートで終端する（拒否理由は「参照系列が無い」
ではなく「strategy を受け付けない」である）。本ファイルは「受付面から到達できないこと」
を固定する役割に変わった。新しい不変条件は
`tests/unit/test_submit_job_strategy_rejection.py` が持つ。

**「エンジン側へ移管済み」とは書かない（実測 2026-08-19）**: エンジン側の run_job 直投入
検定（`tests/integration/test_run_job_strategy.py` / `test_run_job_strategy_e2e.py`）は
無改変で緑だが、そこが固定するのは `strategy_override` の受け渡し・構築失敗の記録・
E2E の決定性であって、**P6-E5 の判定（参照指標 ⊆ EA の登録系列）と 1:1 対応する検定は
無い**（実測: 当該 2 ファイルに missing-series の検定は存在しない）。移管ではなく、
受付面が strategy 本文を受け取らなくなった＝**守る対象そのものが消えた**が正しい。
run_job 直投入経路で参照系列が無い本文を渡した場合の受け皿は、従来どおり実行時
fail-stop（`GenericConditionStrategy` の `IndicatorBufferError`）である。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.domain.simulation_job import JobStatus
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

# TC24051901 は registry に {madiff, close} を持つ（実測）。
_CATALOG = FakeSeriesCatalog({"TC24051901": frozenset({"madiff", "close"})})

#: 段階 2 の受付ゲートが返す文言の目印（P6-E5 の文言と取り違えないための固定点）。
_INTAKE_GATE = "MT5 Settings"


def _interactor(ledger=None, launcher=None, catalog=_CATALOG):
    return SubmitJobInteractor(
        ledger=ledger or FakeLedger(),
        launcher=launcher or FakeLauncher(),
        series_catalog=catalog,
        required_series=required_series,
        stop_loss_catalog=FakeStopLossCatalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )


def _sub(strategy):
    return JobSubmission(backtest={"ea_name": "TC24051901"}, strategy=strategy)


def test_strategy_referencing_available_series_is_rejected_by_intake_gate():
    # Arrange: close は TC の registry にある（段階 1 までは受理されていた本文）
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub({"entry_long": [{"indicator": "close", "shift": 1, "op": ">", "rhs": 1.0}]})
    # Act / Assert: 段階 2 以降は参照系列の可否に関わらず受付で終端する
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


def test_strategy_referencing_missing_series_is_rejected():
    # Arrange: "ema" は TC の registry に無い
    sut = _interactor()
    sub = _sub({"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}]})
    # Act / Assert: 拒否理由は段階 2 のゲート（P6-E5 の「ema が無い」には到達しない）
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)


def test_missing_series_in_rhs_ref_is_rejected():
    # Arrange: rhs 参照の指標 "sma" が無い
    sut = _interactor()
    sub = _sub(
        {
            "entry_long": [
                {"indicator": "close", "shift": 0, "op": ">", "rhs": {"indicator": "sma", "shift": 1}}
            ]
        }
    )
    # Act / Assert: 同上（受付ゲートで終端する）
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)


def test_rejected_strategy_job_leaves_no_residue():
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    sut = _interactor(ledger=ledger, launcher=launcher)
    sub = _sub({"entry_short": [{"indicator": "nope", "shift": 0, "op": "<", "rhs": 1.0}]})
    # Act
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)
    # Assert
    assert ledger.create_calls == 0
    assert launcher.launched == []


def test_strategy_off_skips_validation():
    # Arrange: strategy None（既定 OFF）は検証を巻き込まない（既存挙動 byte 等価）
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    got = sut.execute(JobSubmission(backtest={"ea_name": "TC24051901"}))
    # Assert
    assert got.status == JobStatus.RUNNING.value


def test_strategy_override_is_not_an_accepted_backtest_key():
    # 注入専用の引数（run_job が spec.strategy から組む）は JSON backtest から渡させない。
    # strategy_decorator と同じ扱い（_INJECTED_ONLY_KEYS）。
    from simulator.sim_ui.main.composition_root_jobs import allowed_backtest_keys

    allowed = allowed_backtest_keys()
    assert "strategy_override" not in allowed
    assert "strategy_decorator" not in allowed


def test_strategy_validated_even_when_sizing_off():
    # Arrange: sizing OFF でも strategy 参照検証は効く（独立した検証）
    sut = _interactor()
    sub = JobSubmission(
        backtest={"ea_name": "TC24051901"},
        sizing=None,
        strategy={"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}]},
    )
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)
