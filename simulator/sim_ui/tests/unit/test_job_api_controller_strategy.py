"""JobApiController.submit の strategy 読取（P6-E3）の単体検定.

責務は翻訳のみ: HTTP 本文の ``strategy`` を JobSubmission へ写す。既定（strategy 不在）は
None（byte 等価）。
"""
from __future__ import annotations

import json

from simulator.sim_ui.adapter.job_api_controller import JobApiController
from simulator.sim_ui.usecase.job_models import JobView


class _RecordingSubmit:
    def __init__(self):
        self.seen = None

    def execute(self, submission):
        self.seen = submission
        return JobView(job_id="j1", status="running")


def _controller(submit):
    return JobApiController(submit=submit, query=None, cancel=None, fetch_result=None)


def test_submit_reads_strategy_from_body():
    # Arrange
    submit = _RecordingSubmit()
    ctrl = _controller(submit)
    strategy = {"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}]}
    body = json.dumps({"backtest": {"ea_name": "TC24051901"}, "strategy": strategy}).encode()
    # Act
    resp = ctrl.submit(body)
    # Assert
    assert resp.status == 202
    assert submit.seen.strategy == strategy


def test_submit_without_strategy_is_none():
    # Arrange
    submit = _RecordingSubmit()
    ctrl = _controller(submit)
    body = json.dumps({"backtest": {"ea_name": "TC24051901"}}).encode()
    # Act
    ctrl.submit(body)
    # Assert: strategy 不在は None（既定 OFF・byte 等価）
    assert submit.seen.strategy is None
