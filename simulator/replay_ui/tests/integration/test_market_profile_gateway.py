"""MarketProfileGateway: bridge 委譲の検証（fake bridge 注入）＋ 実 bridge export 到達性。

Gateway は ``api_loader`` の ``handle_market_profile``（indicator_ui controller の純ロジック）
へ委譲し、bridge の (status, body) を PortResult へ翻訳して返す（ISSUE-502 段階 5B）。
serve は本 gateway を Port として注入し、bridge を直 import しない
（DIP）。実 bridge が当該シンボルを export していることも到達性テストで固定する。

★この時点で simulator/replay_ui/adapter/market_profile_gateway.py は未実装（Red）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.replay_ui.adapter.market_profile_gateway import MarketProfileGateway
from simulator.replay_ui.usecase.replay_ports import MarketProfilePort


def _fake_bridge_loader(recorder):
    def handle(ref, timeframe=None, limit=None, bins=None, va=None, src=None,
               barw=None, to=None, **kwargs):
        recorder.append({
            "ref": ref, "timeframe": timeframe, "limit": limit, "bins": bins,
            "va": va, "src": src, "barw": barw, "to": to,
            "from": kwargs.get("from"), "today": kwargs.get("today"),
            "sessions": kwargs.get("sessions"),
        })
        return 200, {"ok": True, "profile": {"bins": []}}

    def load(api_path=None, repo_root=None):
        return SimpleNamespace(handle_market_profile=handle)

    return load


def test_gateway_satisfies_the_market_profile_port_protocol():
    gw = MarketProfileGateway(bridge_loader=_fake_bridge_loader([]))
    assert isinstance(gw, MarketProfilePort)


def test_gateway_delegates_to_bridge_handle_and_threads_to_T():
    # Arrange
    calls = []
    gw = MarketProfileGateway(bridge_loader=_fake_bridge_loader(calls))
    # Act
    result = gw.profile(
        ref="jp225_tick", timeframe="1h", limit=None, bins="60", va="0.7",
        src="candle", barw=None, to=1704074400,
    )
    # Assert: bridge.handle_market_profile へ正しい引数で委譲し PortResult を返す。
    assert result.ok
    assert result.payload["ok"] is True
    call = calls[-1]
    assert call["ref"] == "jp225_tick"
    assert call["timeframe"] == "1h"
    assert call["bins"] == "60"
    assert call["va"] == "0.7"
    assert call["src"] == "candle"
    # to（as-seen-at-t の T）が bridge へ透過している（因果・未来リーク防止）。
    assert call["to"] == 1704074400


def test_gateway_threads_from_today_sessions_via_reserved_kwarg():
    # Arrange
    calls = []
    gw = MarketProfileGateway(bridge_loader=_fake_bridge_loader(calls))
    # Act: 予約語 from / today / sessions を透過する。
    gw.profile(
        ref="jp225_tick", timeframe="1D", limit=None, bins=None, va=None,
        src="dwell", barw=None, to=1000, frm=900, today="1", sessions="1",
    )
    # Assert: 予約語 from が kwargs 経由で bridge へ透過する。
    call = calls[-1]
    assert call["from"] == 900
    assert call["today"] == "1"
    assert call["sessions"] == "1"


def test_gateway_translates_the_bridge_error_into_a_classified_failure():
    """bridge の 400 は validation 分類の失敗になる（番号は Port に現れない）。"""
    def loader(api_path=None, repo_root=None):
        def handle(ref, timeframe=None, **kwargs):
            return 400, {"ok": False, "error": {"type": "validation", "message": "bad"}}
        return SimpleNamespace(handle_market_profile=handle)

    gw = MarketProfileGateway(bridge_loader=loader)
    result = gw.profile(
        ref="bad", timeframe="1h", limit=None, bins=None, va=None, src=None,
        barw=None, to=None,
    )
    assert (result.ok, result.error_type) == (False, "validation")
    # ボディは無改変で運ぶ（応答 byte が変わらない根拠）。
    assert result.payload == {"ok": False, "error": {"type": "validation", "message": "bad"}}


def test_gateway_refuses_a_status_that_contradicts_the_body():
    """番号と分類が食い違う bridge 応答は通さない（fail-closed）。

    通してしまうと、どちらを採ったかで応答 byte が静かに変わる。
    """
    from simulator.replay_ui.adapter.bridge_result import BridgeContractError

    def loader(api_path=None, repo_root=None):
        def handle(ref, timeframe=None, **kwargs):
            # 分類は validation（正典は 400）なのに 200 を名乗る。
            return 200, {"ok": False, "error": {"type": "validation", "message": "bad"}}
        return SimpleNamespace(handle_market_profile=handle)

    gw = MarketProfileGateway(bridge_loader=loader)
    with pytest.raises(BridgeContractError):
        gw.profile(ref="bad", timeframe="1h", limit=None, bins=None, va=None, src=None,
                   barw=None, to=None)


def test_real_bridge_exports_handle_market_profile():
    from indigators.indicator_ui import api_loader
    ns = api_loader.load()
    assert callable(getattr(ns, "handle_market_profile", None))
