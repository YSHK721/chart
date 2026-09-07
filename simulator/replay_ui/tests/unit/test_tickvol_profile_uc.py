"""UC tickvol_profile: fake TickvolProfilePort 注入の thin delegation 検証（AAA）。

usecase は Port へ (ref, sessions, pct, until) を素通し委譲し :class:`PortResult` をそのまま返す
（薄い委譲＝独自ロジック無し）。ISSUE-502 段階 5B: Port は HTTP ステータスを返さない
（分類 → 番号の写像は framework 層の写像関数 1 箇所）。``until`` は必ずリビール T を透過する（当日を集計に含めない＝
因果・未来リーク防止）。
"""
from __future__ import annotations

from simulator.replay_ui.adapter.tickvol_profile_gateway import TickvolProfileGateway
from simulator.replay_ui.usecase.port_result import PortResult
from simulator.replay_ui.usecase.tickvol_profile import (
    TickvolProfileRequest,
    tickvol_profile,
)


class _FakeProfilePort:
    def __init__(self, ret=PortResult.success({"ok": True})):
        self.ret = ret
        self.calls = []

    def profile(self, ref, sessions=None, pct=None, until=None):
        self.calls.append({"ref": ref, "sessions": sessions, "pct": pct, "until": until})
        return self.ret


def test_delegates_all_args_and_returns_port_status_body_verbatim():
    # Arrange
    port = _FakeProfilePort(
        ret=PortResult.success({"ok": True, "bands": [{"startOff": 0, "endOff": 900}]}))
    req = TickvolProfileRequest(ref="jp225_tick", sessions=20, pct=75, until=1704074400)
    # Act
    result = tickvol_profile(request=req, profile_port=port)
    # Assert: 素通し委譲（PortResult verbatim・引数完全一致）。
    assert result.ok
    assert result.payload == {"ok": True, "bands": [{"startOff": 0, "endOff": 900}]}
    assert port.calls == [
        {"ref": "jp225_tick", "sessions": 20, "pct": 75, "until": 1704074400}
    ]


def test_threads_until_T_through_even_when_optional_params_are_absent():
    # Arrange: 因果 T（until）だけを渡す（sessions/pct は既定に委ねる）。
    port = _FakeProfilePort()
    req = TickvolProfileRequest(ref="jp225_tick", until=1000)
    # Act
    tickvol_profile(request=req, profile_port=port)
    # Assert
    assert port.calls[0]["until"] == 1000
    assert port.calls[0]["sessions"] is None
    assert port.calls[0]["pct"] is None


def test_passes_through_the_failure_classification_from_port():
    # Arrange: 未知 ref → port が validation 分類の失敗を返すケースをそのまま返す。
    port = _FakeProfilePort(ret=PortResult.failure(
        "validation", {"ok": False, "error": {"type": "validation", "message": "bad"}}))
    # Act
    result = tickvol_profile(request=TickvolProfileRequest(ref="bad"), profile_port=port)
    # Assert: 分類はそのまま。HTTP ステータスは usecase に現れない（DIP）。
    assert (result.ok, result.error_type) == (False, "validation")
    assert result.payload["error"]["type"] == "validation"


# --------------------------------------------------------------------------- #
# gateway（bridge 委譲）: fake loader で indicator_ui 実体に依存せず呼出形を固定する
# --------------------------------------------------------------------------- #
class _FakeBridge:
    def __init__(self):
        self.calls = []

    def handle_tickvol_profile(self, ref, sessions, pct, until):
        self.calls.append((ref, sessions, pct, until))
        return 200, {"ok": True}


def test_gateway_delegates_to_bridge_handler_positionally():
    # Arrange
    fake = _FakeBridge()
    gw = TickvolProfileGateway(bridge_loader=lambda *_a, **_k: fake)
    # Act
    result = gw.profile("jp225_tick", sessions=20, pct=75, until=1234)
    # Assert: ライブ側 controller の純ロジックへ素通し（DRY・ライブと byte 一致の根拠）。
    #   gateway は bridge の (status, body) を PortResult へ翻訳する（ボディは無改変）。
    assert (result.ok, result.payload) == (True, {"ok": True})
    assert fake.calls == [("jp225_tick", 20, 75, 1234)]
