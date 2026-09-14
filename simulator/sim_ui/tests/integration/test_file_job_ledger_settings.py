"""`FileJobLedger` が spec.json へ `settings`（第 4 ブロック）を残すことの検定。

固定する不変条件:
    1. settings 有りの投入は `spec.json` に `settings` ブロックを持ち、
       `tester`（生トークン）と `inputs`（行原文）をそのまま保存する。
       子プロセス（`run_job`）が読む唯一の受け渡し口である。
    2. settings 不在の投入の `spec.json` は**現行のキー構成のまま**（`settings` は
       `null`）。旧 spec（`settings` キーそのものが無い形）も子が受け取り得るため、
       値は `null` であって「キーを消す」ではない（Phase 6 の `strategy` と同型）。

キー一覧の出所（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.6.1）:
    本ファイルは元々 ``["backtest", "sizing", "strategy", "settings"]`` を**手書き**で
    表明していた。台帳側の手書き dict 列挙を `fields(JobSubmission)` からの機械導出へ
    置換した（＝ブロックが増えても取り残されない）以上、期待値の側だけを手書きで
    残すと、**次のブロックで取り残されるのは検定の方**になる。設計書 §6.6.1 の
    「期待値の名前一覧をテストへ書かない」に従い、ここでも宣言から導く。
    ブロックが端から端まで結線されていることは
    `sim_ui/tests/integration/test_job_blocks_are_wired_end_to_end.py` が別に固定する。
"""
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from simulator.sim_ui.adapter.file_job_ledger import FileJobLedger
from simulator.sim_ui.usecase.job_models import JobSubmission

#: `spec.json` が持つべきキー（宣言から導く・手書きしない）。
_BLOCK_NAMES = [f.name for f in fields(JobSubmission)]


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
    assert list(spec) == _BLOCK_NAMES
    assert spec["settings"] == settings
    # 正の対照: 宣言が空なら上の一致は恒真になる。
    assert "settings" in _BLOCK_NAMES and len(_BLOCK_NAMES) >= 4, _BLOCK_NAMES


def test_settings不在では第4ブロックがnullで他3ブロックは現行のまま(tmp_path: Path) -> None:
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
