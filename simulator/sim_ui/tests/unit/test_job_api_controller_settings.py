"""`JobApiController` が settings ブロックを usecase まで運ぶことの検定（Phase 8）。

なぜ要るか（実測された壊れ方の型・ISSUE-291「受け口だけ作って呼び出し側が送らない」）:
受付検証・台帳・実行がすべて `settings` を扱えても、HTTP 境界の変換が本文の
`settings` を読まなければ**投入は 202 で通り、旧経路で走る**。結果は出るので誰も
気づかない。境界の 1 行を独立に固定する。
"""
from __future__ import annotations

import json

from simulator.sim_ui.adapter.job_api_controller import JobApiController
from simulator.sim_ui.usecase.job_models import JobView


class _SpySubmit:
    def __init__(self) -> None:
        self.submission = None

    def execute(self, submission):
        self.submission = submission
        return JobView(job_id="j1", status="running")


def _post(body: dict) -> "_SpySubmit":
    spy = _SpySubmit()
    controller = JobApiController(submit=spy, query=None, cancel=None, fetch_result=None)
    response = controller.submit(json.dumps(body).encode("utf-8"))
    assert response.status == 202, response.payload
    return spy


_SETTINGS = {"tester": {"Symbol": "JP225", "Period": "M1", "Model": "1"}, "inputs": []}


def test_本文のsettingsがusecaseまで届く() -> None:
    # Arrange / Act
    spy = _post({"backtest": {"ea_name": "X"}, "settings": _SETTINGS})
    # Assert
    assert spy.submission.settings == _SETTINGS


def test_settings不在はNoneとして渡る() -> None:
    """旧 spec（settings キーの無い本文）との併存。既定 OFF＝現行経路。"""
    # Arrange / Act
    spy = _post({"backtest": {"ea_name": "X"}})
    # Assert
    assert spy.submission.settings is None
