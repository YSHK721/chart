"""SubmitJobInteractor の建玉変更（Phase 7 FR-07/08）受付検証の単体検定。

裁定: strategy.trailing / partial_close サブブロックを受理する（未指定=OFF=byte 等価）。
構造が壊れている（マッピングでない）場合は受付時に明示拒否する。範囲・列挙の意味検証は
run_job の framework loader が fail-stop で担う（受付は構造のみ・二重化しない）。

**段階 2（§19.5）以降の位置づけ**: `strategy` ブロックを持つ投入は `execute` 冒頭の
受付ゲート（`_reject_strategy_block`）で拒否されるため、上記 Phase 7 の受付検証
（構造検査・粒度ゲート）は **到達不能**になった（検証コードは可逆性のため残置）。
本ファイルで `execute` を呼ぶ検定はすべて段階 2 のゲートで終端する。本ファイルは
「受付面から到達できないこと」を固定する役割に変わった。新しい不変条件は
`tests/unit/test_submit_job_strategy_rejection.py` が持つ。

**「エンジン側へ移管済み」とは書かない（実測 2026-08-19）**: エンジン側の run_job 直投入
検定（`tests/integration/test_run_job_position_manager.py` /
`test_run_job_settings_extensions.py`）は無改変で緑であり、`position_manager` の
仕様読込と作動はエンジン側（`simulator/tests/unit/test_position_manager_spec_loader.py` /
`simulator/tests/integration/test_position_manager_engine.py`）が固定する。ただし
**粒度ゲートの判定（trailing.granularity と run の実効粒度の突き合わせ）と 1:1 対応する
検定はエンジン側に無い**（実測: loader は granularity の列挙のみを検証し、run の
tick_model との一致は見ない）。移管ではなく、受付面が strategy 本文を受け取らなくなった
＝**守る対象そのものが消えた**が正しい（run_job 直投入経路は元から受付を通らない）。
"""
from __future__ import annotations

import pytest

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

_CATALOG = FakeSeriesCatalog({"TC24051901": frozenset({"madiff", "close"})})


def _interactor(launcher=None):
    return SubmitJobInteractor(
        ledger=FakeLedger(),
        launcher=launcher or FakeLauncher(),
        series_catalog=_CATALOG,
        required_series=required_series,
        stop_loss_catalog=FakeStopLossCatalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )


_ENTRY = [{"indicator": "close", "shift": 1, "op": ">", "rhs": 1.0}]

#: 段階 2 の受付ゲートが返す文言の目印（Phase 7 の文言と取り違えないための固定点）。
_INTAKE_GATE = "MT5 Settings"


def _sub(strategy):
    return JobSubmission(backtest={"ea_name": "TC24051901"}, strategy=strategy)


def test_trailing_mapping_is_rejected_by_intake_gate():
    # 段階 1 までは受理されていた本文。段階 2 以降は受付で終端する。
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub({"entry_long": _ENTRY,
                "trailing": {"trigger_points": 50, "distance_points": 30}})
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


def test_partial_close_mapping_is_rejected_by_intake_gate():
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub({"entry_long": _ENTRY,
                "partial_close": {"trigger": {"profit_points": 50}, "close_fraction": 0.5}})
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


def test_non_mapping_trailing_is_rejected_by_intake_gate():
    # 構造が壊れていても（Phase 7 なら構造検査が拒否した本文でも）受付で終端する。
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub({"entry_long": _ENTRY, "trailing": [1, 2, 3]})
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


def test_non_mapping_partial_close_is_rejected_by_intake_gate():
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub({"entry_long": _ENTRY, "partial_close": "nope"})
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


# --- 粒度不一致 fail-stop（🟡・無言不作動の防止） ---------------------------

def _sub_with_tickmodel(strategy, tick_model=None):
    overrides = {} if tick_model is None else {"tick_model": tick_model}
    return JobSubmission(
        backtest={"ea_name": "TC24051901", "config_overrides": overrides},
        strategy=strategy,
    )


def test_real_ticks_run_with_bar_trailing_is_rejected_by_intake_gate():
    # real_ticks 実行 ＋ granularity="bar"（Phase 7 なら粒度ゲートが拒否した本文）でも
    # 拒否理由は受付ゲートである（粒度ゲートには到達しない）。
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY, "trailing": {"granularity": "bar",
                                            "trigger_points": 50, "distance_points": 30}},
        tick_model="real_ticks",
    )
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


def test_real_ticks_run_with_tick_trailing_is_rejected_by_intake_gate():
    # 粒度が一致していても（＝Phase 7 の粒度ゲートは通る本文でも）受付で終端する。
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY, "trailing": {"granularity": "tick",
                                            "trigger_points": 50, "distance_points": 30}},
        tick_model="real_ticks",
    )
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


def test_bar_run_with_tick_trailing_is_rejected_by_intake_gate():
    # 既定（every_tick＝bar 経路）＋ granularity="tick"（Phase 7 なら粒度ゲートが拒否）。
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY, "trailing": {"granularity": "tick",
                                            "trigger_points": 50, "distance_points": 30}},
    )
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


def test_bar_run_with_bar_trailing_default_is_rejected_by_intake_gate():
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    # granularity 省略（既定 bar）＋ bar 実行 → Phase 7 では一致＝受理だった本文。
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY, "trailing": {"trigger_points": 50, "distance_points": 30}},
    )
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []


def test_partial_close_on_real_ticks_is_rejected_by_intake_gate():
    # partial_close は粒度非依存＝Phase 7 の粒度ゲート対象外だが、段階 2 では拒否対象。
    # 名前に real_ticks を残すのは、上の `..._mapping_is_rejected_by_intake_gate`
    # （粒度指定なし）と区別する条件がこれだけだから（旧名 `..._has_no_granularity_gate`
    # が持っていた識別子を、改名で落とさない）。
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY,
         "partial_close": {"trigger": {"profit_points": 50}, "close_fraction": 0.5}},
        tick_model="real_ticks",
    )
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert _INTAKE_GATE in str(exc.value)
    assert launcher.launched == []
