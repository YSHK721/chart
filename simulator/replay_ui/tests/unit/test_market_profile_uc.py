"""UC market_profile: fake MarketProfilePort 注入の thin delegation 検証（AAA）。

usecase は Port へ (ref,timeframe,limit,bins,va,src,barw,to,frm,today,sessions) を素通し委譲し、
(status, body) をそのまま返す（薄い委譲＝独自ロジック無し）。to は必ずリビール T を透過する
（因果＝as-seen-at-t・未来リーク防止）。

★この時点で simulator/replay_ui/usecase/market_profile.py は未実装（Red）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.market_profile import (
    MarketProfileRequest,
    market_profile,
)


class _FakeProfilePort:
    def __init__(self, ret=(200, {"ok": True})):
        self.ret = ret
        self.calls = []

    def profile(self, ref, timeframe, limit, bins, va, src, barw, to,
                frm=None, today=None, sessions=None):
        self.calls.append(
            {"ref": ref, "timeframe": timeframe, "limit": limit, "bins": bins,
             "va": va, "src": src, "barw": barw, "to": to, "frm": frm,
             "today": today, "sessions": sessions}
        )
        return self.ret


def test_delegates_all_args_and_returns_port_status_body_verbatim():
    # Arrange
    port = _FakeProfilePort(ret=(200, {"ok": True, "profile": {"bins": []}}))
    req = MarketProfileRequest(
        ref="jp225_tick", timeframe="1h", limit=None, bins="60", va="0.7",
        src="candle", barw=None, to=1704074400,
    )
    # Act
    status, body = market_profile(request=req, profile_port=port)
    # Assert: 素通し委譲（status/body verbatim・引数完全一致）。
    assert status == 200
    assert body == {"ok": True, "profile": {"bins": []}}
    assert port.calls == [{
        "ref": "jp225_tick", "timeframe": "1h", "limit": None, "bins": "60",
        "va": "0.7", "src": "candle", "barw": None, "to": 1704074400,
        "frm": None, "today": None, "sessions": None,
    }]


def test_threads_to_T_as_seen_at_t_and_optional_params_through():
    # Arrange: 因果 T（to）・sessions・today・from を透過することを確認する。
    port = _FakeProfilePort(ret=(200, {"ok": True}))
    req = MarketProfileRequest(
        ref="jp225_tick", timeframe="1D", limit="100", bins=None, va=None,
        src="dwell", barw="5", to=1000, frm=900, today="1", sessions="1",
    )
    # Act
    market_profile(request=req, profile_port=port)
    # Assert
    call = port.calls[0]
    assert call["to"] == 1000
    assert call["frm"] == 900
    assert call["today"] == "1"
    assert call["sessions"] == "1"
    assert call["src"] == "dwell"
    assert call["barw"] == "5"


def test_passes_through_validation_status_from_port():
    # Arrange: 未知 ref → port が 400 を返すケースをそのまま返す。
    port = _FakeProfilePort(ret=(400, {"error": {"type": "validation", "message": "bad"}}))
    req = MarketProfileRequest(ref="bad", timeframe="1h", limit=None, bins=None,
                              va=None, src=None, barw=None, to=None)
    # Act
    status, body = market_profile(request=req, profile_port=port)
    # Assert
    assert status == 400
    assert body["error"]["type"] == "validation"
