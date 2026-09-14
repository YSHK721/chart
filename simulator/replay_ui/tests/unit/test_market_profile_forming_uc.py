"""UC market_profile_forming: fake MarketProfileFormingPort 注入の thin delegation 検証（AAA）。

usecase は Port へ (ref,timeframe,now,base,since,bins,va,barw) を素通し委譲し、(status, body) を
そのまま返す（薄い委譲＝独自ロジック無し）。now は必ずリビール T を透過する（因果・未来リーク防止）。

★この時点で simulator/replay_ui/usecase/market_profile_forming.py は未実装（Red）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.market_profile_forming import (
    MarketProfileFormingRequest,
    market_profile_forming,
)


class _FakeFormingPort:
    def __init__(self, ret=(200, {"ok": True})):
        self.ret = ret
        self.calls = []

    def forming(self, ref, timeframe, now, base, since, bins, va, barw):
        self.calls.append(
            {"ref": ref, "timeframe": timeframe, "now": now, "base": base,
             "since": since, "bins": bins, "va": va, "barw": barw}
        )
        return self.ret


def test_delegates_all_args_and_returns_port_status_body_verbatim():
    # Arrange
    port = _FakeFormingPort(ret=(200, {"ok": True, "formingStart": 123, "ticks": [[10, 1.0]]}))
    req = MarketProfileFormingRequest(
        ref="jp225_tick", timeframe="1h", now=1704074400, base=1,
        since=None, bins=None, va=None, barw=None,
    )
    # Act
    status, body = market_profile_forming(request=req, forming_port=port)
    # Assert: 素通し委譲（status/body verbatim・引数完全一致）。
    assert status == 200
    assert body == {"ok": True, "formingStart": 123, "ticks": [[10, 1.0]]}
    assert port.calls == [{
        "ref": "jp225_tick", "timeframe": "1h", "now": 1704074400, "base": 1,
        "since": None, "bins": None, "va": None, "barw": None,
    }]


def test_threads_now_T_and_since_base_barw_through_without_mutation():
    # Arrange: 因果 T（now）・since・base=0・barw を透過することを確認する。
    port = _FakeFormingPort(ret=(200, {"ok": True}))
    req = MarketProfileFormingRequest(
        ref="jp225_tick", timeframe="1D", now=1000, base=0, since=900, bins=None, va="0.7", barw="5",
    )
    # Act
    market_profile_forming(request=req, forming_port=port)
    # Assert
    assert port.calls[0]["now"] == 1000
    assert port.calls[0]["base"] == 0
    assert port.calls[0]["since"] == 900
    assert port.calls[0]["barw"] == "5"


class _FakeFormingPortFrm:
    """frm（セッション窓下限）を受け取れる fake port（新規 additive 引数の透過検証用）。"""

    def __init__(self, ret=(200, {"ok": True})):
        self.ret = ret
        self.calls = []

    def forming(self, ref, timeframe, now, base, since, bins, va, barw, frm=None):
        self.calls.append({"now": now, "frm": frm})
        return self.ret


def test_threads_frm_session_window_through_to_port():
    # Arrange: セッション窓 MP の base 下限 frm（当日始まり）を request に載せる。
    port = _FakeFormingPortFrm()
    req = MarketProfileFormingRequest(
        ref="jp225_tick", timeframe="1h", now=1704074400, base=1, frm=1704067200,
    )
    # Act
    market_profile_forming(request=req, forming_port=port)
    # Assert: frm が port.forming へ透過している（当日始まり=1704067200）。
    assert port.calls[0]["frm"] == 1704067200


def test_frm_omitted_does_not_pass_frm_backward_compat():
    # Arrange: frm 省略時は既存 8 引数 port（frm 引数なし）でも動く＝後方互換（frm を渡さない）。
    port = _FakeFormingPort()  # 既存 fake（forming(... , barw) は frm を受けない）。
    req = MarketProfileFormingRequest(ref="jp225_tick", timeframe="1h", now=7, base=1)
    # Act: frm=None のとき frm を透過しないので TypeError にならない。
    status, _ = market_profile_forming(request=req, forming_port=port)
    # Assert
    assert status == 200
    assert port.calls[0]["now"] == 7


def test_passes_through_validation_status_from_port():
    # Arrange: 非 tick ref → port が 400 を返すケースをそのまま返す。
    port = _FakeFormingPort(ret=(400, {"error": {"type": "validation", "message": "bad"}}))
    req = MarketProfileFormingRequest(ref="bad", timeframe="1h", now=1, base=1)
    # Act
    status, body = market_profile_forming(request=req, forming_port=port)
    # Assert
    assert status == 400
    assert body["error"]["type"] == "validation"
