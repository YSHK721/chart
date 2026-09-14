"""FileJobLedger の spec.json への strategy ブロック直列化（P6-E1）の結合検定.

現状 spec.json は {backtest, sizing} のみ。Phase 6 で strategy を追加する（既定 OFF＝
strategy 不在の投入では ``"strategy": null`` になり、子プロセス側の解釈は byte 等価）。
"""
from __future__ import annotations

import json
from pathlib import Path

from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.usecase.job_models import JobSubmission


def test_spec_json_persists_strategy_block(tmp_path):
    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    strategy = {"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}]}
    sub = JobSubmission(backtest={"ea_name": "TC24051901"}, strategy=strategy)
    # Act
    job = ledger.create(sub)
    spec = json.loads((tmp_path / job.job_id / "spec.json").read_text(encoding="utf-8"))
    # Assert
    assert spec["strategy"] == strategy
    assert spec["backtest"] == {"ea_name": "TC24051901"}


def test_spec_json_strategy_is_null_when_absent(tmp_path):
    # Arrange: strategy 不在（既定 OFF）→ "strategy": null（byte 等価の維持）
    ledger = FileJobLedger(data_root=tmp_path)
    sub = JobSubmission(backtest={"ea_name": "TC24051901"})
    # Act
    job = ledger.create(sub)
    spec = json.loads((tmp_path / job.job_id / "spec.json").read_text(encoding="utf-8"))
    # Assert
    assert spec["strategy"] is None
