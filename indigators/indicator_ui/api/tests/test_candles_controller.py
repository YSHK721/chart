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


def test_candles_time_range_is_passed_through(monkeypatch):
    """from/to（UNIX 秒）は範囲として下位へ渡る（2026-09-26 承認）。"""
    seen = {}

    def _load(ref, tf, limit, *, start=None, end=None):
        seen.update(start=start, end=end)
        return [{"time": 1}]

    monkeypatch.setattr(cc.dataset, "load_candles", _load)
    st, _body = cc.handle_candles("jp225_tick", "1m", None, "100", "200")
    assert st == 200 and seen == {"start": 100, "end": 200}


def test_candles_without_a_range_keep_the_old_call(monkeypatch):
    """範囲を指定しない呼出は従来の 3 引数のまま（範囲を知らない供給実装を壊さない）。"""
    monkeypatch.setattr(cc.dataset, "load_candles", lambda ref, tf, limit: [{"time": 1}])
    st, _body = cc.handle_candles("jp225_tick", "1m", "10", None, "")
    assert st == 200


def test_a_non_integer_range_is_rejected_not_ignored():
    """整数でない from/to は範囲なしへ倒さず 400（別の足をその期間の足として返さない）。"""
    st, body = cc.handle_candles("jp225_tick", "1m", None, "abc", None)
    assert st == 400 and body["ok"] is False
