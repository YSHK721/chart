"""需要台帳と起動時の温め（ISSUE-501 段階 2）の検定。

計算量テスト（CLAUDE.md 絶対命令）:
    - 台帳の書き込みの発行 − 束の変化 = 0（毎秒のポーリングで毎秒書かない）。
    - 温めの再演の発行は台帳 1 冊につき 1 回・台帳なしは 0 回。
"""
from __future__ import annotations

import json

import pytest

from dashboard_ui.adapter.controller import demand_ledger
from dashboard_ui.adapter.controller.demand_ledger import (
    DemandRecordingController,
    replay_recorded_demand,
)

_BUNDLE = {
    "dataset_ref": "jp225_tick",
    "chart_timeframe": "1D",
    "instances": [{"indicator_id": "ma_marod", "timeframe": "1D", "params": {}}],
}


class _Inner:
    """Test Spy: handle の発行を数える最小 controller。"""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.requests: "list[dict]" = []

    def handle(self, request):
        self.requests.append(dict(request))
        return {"ok": self.ok}


def test_a_successful_request_records_the_bundle_without_volatiles(tmp_path) -> None:
    path = tmp_path / "demand.json"
    controller = DemandRecordingController(_Inner(), ledger_path=path)

    controller.handle({**_BUNDLE, "mode": "tick", "known_state": "abc"})

    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert recorded == _BUNDLE   # mode / known_state は載らない


@pytest.mark.parametrize("repeats", [3, 10])
def test_ledger_writes_equal_bundle_changes(
    tmp_path, monkeypatch: pytest.MonkeyPatch, repeats: int
) -> None:
    """書き込みの発行 − 束の変化 = 0（同じ束の再要求 N 回で不変・N の 2 点で固定）。"""
    # Arrange
    writes = [0]
    original = demand_ledger._write_atomic

    def counting(path, text):
        writes[0] += 1
        original(path, text)

    monkeypatch.setattr(demand_ledger, "_write_atomic", counting)
    controller = DemandRecordingController(_Inner(), ledger_path=tmp_path / "d.json")

    # Act: 同じ束を N 回、変えた束を 1 回。
    for _ in range(repeats):
        controller.handle(dict(_BUNDLE))
    changed = {**_BUNDLE, "chart_timeframe": "1h"}
    controller.handle(changed)

    # Assert: 発行 = 束の変化（初回 + 変更 1 回）。
    assert writes[0] == 2


def test_a_failed_request_is_not_recorded(tmp_path) -> None:
    path = tmp_path / "demand.json"
    DemandRecordingController(_Inner(ok=False), ledger_path=path).handle(dict(_BUNDLE))
    assert not path.exists()


def test_replay_issues_exactly_one_full_request(tmp_path) -> None:
    path = tmp_path / "demand.json"
    path.write_text(json.dumps(_BUNDLE, ensure_ascii=False), encoding="utf-8")
    inner = _Inner()

    issued = replay_recorded_demand(lambda: inner, path)

    assert issued is True
    assert len(inner.requests) == 1
    assert inner.requests[0]["mode"] == "full"
    assert inner.requests[0]["instances"] == _BUNDLE["instances"]


@pytest.mark.parametrize("content", ["{broken", "{}", '{"instances": []}'])
def test_replay_with_an_unusable_ledger_issues_nothing(tmp_path, content) -> None:
    """破損・空束の台帳では再演を発行しない（発行 0 のまま正常起動）。"""
    path = tmp_path / "demand.json"
    path.write_text(content, encoding="utf-8")
    inner = _Inner()

    issued = replay_recorded_demand(lambda: inner, path)

    assert issued is False
    assert inner.requests == []


def test_replay_without_a_ledger_file_issues_nothing(tmp_path) -> None:
    """台帳ファイルなし（初回起動）でも再演を発行しない。"""
    inner = _Inner()

    issued = replay_recorded_demand(lambda: inner, tmp_path / "absent.json")

    assert issued is False
    assert inner.requests == []
