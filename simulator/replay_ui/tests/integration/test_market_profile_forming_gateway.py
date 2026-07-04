"""MarketProfileFormingGateway: bridge 委譲の検証（fake bridge 注入）＋ 実 bridge export 到達性。

Gateway は ``_indicator_ui_bridge`` の ``handle_market_profile_forming``（indicator_ui controller の
純ロジック）へ委譲し、(status, body) を返す。serve は本 gateway を Port として注入し、bridge を
直 import しない（DIP）。実 bridge が当該シンボルを export していることも到達性テストで固定する。

★この時点で simulator/replay_ui/adapter/market_profile_forming_gateway.py は未実装（Red）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.replay_ui.adapter.market_profile_forming_gateway import (
    MarketProfileFormingGateway,
)
from simulator.replay_ui.usecase.replay_ports import MarketProfileFormingPort


def _fake_bridge_loader(recorder):
    def handle(ref, timeframe=None, since=None, base=None, now=None, bins=None, va=None, barw=None):
        recorder.append({
            "ref": ref, "timeframe": timeframe, "since": since, "base": base,
            "now": now, "bins": bins, "va": va, "barw": barw,
        })
        return 200, {"ok": True, "formingStart": 555, "now": now}

    def load(api_path=None, repo_root=None):
        return SimpleNamespace(handle_market_profile_forming=handle)

    return load


def test_gateway_satisfies_the_forming_port_protocol():
    # Arrange / Act
    gw = MarketProfileFormingGateway(bridge_loader=_fake_bridge_loader([]))
    # Assert: runtime_checkable Protocol を満たす（serve が Port として注入できる）。
    assert isinstance(gw, MarketProfileFormingPort)


def test_gateway_delegates_to_bridge_handle_and_threads_now_T():
    # Arrange
    calls = []
    gw = MarketProfileFormingGateway(bridge_loader=_fake_bridge_loader(calls))
    # Act
    status, body = gw.forming(
        ref="jp225_tick", timeframe="1h", now=1704074400, base=1,
        since=None, bins=None, va=None, barw=None,
    )
    # Assert: bridge.handle_market_profile_forming へ正しい引数で委譲し (status, body) を返す。
    assert status == 200
    assert body["formingStart"] == 555
    assert calls == [{
        "ref": "jp225_tick", "timeframe": "1h", "since": None, "base": 1,
        "now": 1704074400, "bins": None, "va": None, "barw": None,
    }]
    # now（リビール T）が bridge へ透過している（因果・未来リーク防止）。
    assert body["now"] == 1704074400


def _fake_bridge_loader_frm(recorder):
    """frm（セッション窓下限）を受け取れる fake bridge（新規 additive 引数の透過検証用）。"""

    def handle(ref, timeframe=None, since=None, base=None, now=None, bins=None, va=None,
               barw=None, frm=None):
        recorder.append({"now": now, "frm": frm})
        return 200, {"ok": True, "now": now}

    def load(api_path=None, repo_root=None):
        return SimpleNamespace(handle_market_profile_forming=handle)

    return load


def test_gateway_threads_frm_session_window_to_bridge():
    # Arrange
    calls = []
    gw = MarketProfileFormingGateway(bridge_loader=_fake_bridge_loader_frm(calls))
    # Act: セッション窓 base 下限 frm（当日始まり）を渡す。
    status, _ = gw.forming(
        ref="jp225_tick", timeframe="1h", now=1704074400, base=1,
        since=None, bins=None, va=None, barw=None, frm=1704067200,
    )
    # Assert: bridge.handle_market_profile_forming へ frm が透過する。
    assert status == 200
    assert calls[0]["frm"] == 1704067200


def test_gateway_frm_omitted_does_not_pass_frm_backward_compat():
    # Arrange: frm 省略時は既存 fake bridge（frm 引数なし）でも動く＝後方互換（frm を渡さない）。
    calls = []
    gw = MarketProfileFormingGateway(bridge_loader=_fake_bridge_loader(calls))
    # Act
    status, _ = gw.forming(
        ref="jp225_tick", timeframe="1h", now=1704074400, base=1,
        since=None, bins=None, va=None, barw=None,
    )
    # Assert: frm 未指定 → 既存 8 引数 bridge でも TypeError にならない。
    assert status == 200
    assert calls[0]["now"] == 1704074400


def test_real_bridge_exports_handle_market_profile_forming():
    # Arrange: 実 _indicator_ui_bridge.load の namespace に handle_market_profile_forming がある。
    from simulator.replay_ui.adapter import _indicator_ui_bridge
    ns = _indicator_ui_bridge.load()
    # Assert: export 到達性（callable）。
    assert callable(getattr(ns, "handle_market_profile_forming", None))
