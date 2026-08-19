"""`FileJobLedger` が spec.json へ `settings`（第 4 ブロック）を残すことの検定。

固定する不変条件:
    1. settings 有りの投入は `spec.json` に 4 番目のキー `settings` を持ち、
       `tester`（生トークン）と `inputs`（行原文）をそのまま保存する。
       子プロセス（`run_job`）が読む唯一の受け渡し口である。
    2. settings 不在の投入の `spec.json` は**現行のキー構成のまま**（`settings` は
       `null`）。旧 spec（`settings` キーそのものが無い形）も子が受け取り得るため、
       値は `null` であって「キーを消す」ではない（Phase 6 の `strategy` と同型）。
"""
from __future__ import annotations

import json
from pathlib import Path

from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.usecase.job_models import JobSubmission


def _spec_of(ledger: FileJobLedger, job_id: str) -> dict:
    return json.loads((ledger.job_dir(job_id) / "spec.json").read_text(encoding="utf-8"))


def test_settingsは第4ブロックとして保存される(tmp_path: Path) -> None:
    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    settings = {"tester": {"Symbol": "JP225", "Period": "M1", "Model": "1"}, "inputs": []}
    # Act
    job = ledger.create(JobSubmission(backtest={"ea_name": "X"}, settings=settings))
    # Assert
    spec = _spec_of(ledger, job.job_id)
    assert list(spec) == ["backtest", "sizing", "strategy", "settings"]
    assert spec["settings"] == settings


def test_settings不在ではキー構成が現行のまま(tmp_path: Path) -> None:
    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    # Act
    job = ledger.create(JobSubmission(backtest={"ea_name": "X"}))
    # Assert
    spec = _spec_of(ledger, job.job_id)
    assert spec["settings"] is None
    assert spec["backtest"] == {"ea_name": "X"}
    assert spec["sizing"] is None
    assert spec["strategy"] is None


def test_生トークンをそのまま保存する(tmp_path: Path) -> None:
    """型付き DTO へ変換して保存しない（検証の第 2 実装・往復の破れを作らない）。"""
    # Arrange
    ledger = FileJobLedger(data_root=tmp_path)
    settings = {
        "tester": {"Deposit": "139500.50", "Model": "1"},
        "inputs": ["MAPeriod=3||2||1||22||Y"],
    }
    # Act
    job = ledger.create(JobSubmission(backtest={"ea_name": "X"}, settings=settings))
    # Assert
    assert _spec_of(ledger, job.job_id)["settings"] == settings
