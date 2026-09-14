"""candles_controller（/candles・/forming_bar の純ロジック・ISSUE-087 🟡-1）の検証。"""
from __future__ import annotations

from adapter.controller import candles_controller as cc


def test_candles_unknown_ref_400():
    st, body = cc.handle_candles("nope", None, None)
    assert st == 400 and body["ok"] is False


def test_candles_unknown_timeframe_400():
    st, body = cc.handle_candles("jp225_tick", "3h", None)
    assert st == 400


def test_candles_happy(monkeypatch):
    monkeypatch.setattr(cc.dataset, "load_candles", lambda ref, tf, limit: [{"time": 1}])
    st, body = cc.handle_candles("jp225_tick", "1D", "10")
    assert st == 200 and body["candles"] == [{"time": 1}]


def test_forming_bar_fallback_chain(monkeypatch):
    calls = []
    monkeypatch.setattr(cc.forming_bar_mod, "rollup_forming_bar",
                        lambda *a, **k: calls.append("rollup") or None)
    monkeypatch.setattr(cc.forming_bar_mod, "forming_bar",
                        lambda *a, **k: calls.append("parquet") or None)

    class _Buf:
        def ticks_since(self, ms):
            return [[1784117700 * 1000 + 1000, 100.0]]

    st, body = cc.handle_forming_bar("jp225_tick", "5m", "1784117760", buffer=_Buf())
    assert st == 200 and body["ok"] is True
    assert calls == ["rollup", "parquet"], "ロールアップ→parquet→buffer の順にフォールバック"
    assert body["bar"] is not None and body["bar"]["open"] == 100.0


def test_forming_bar_unknown_ref_400():
    st, _ = cc.handle_forming_bar("nope", "5m", None)
    assert st == 400
