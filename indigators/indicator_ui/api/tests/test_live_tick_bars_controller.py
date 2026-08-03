"""/live_ticks のバー帰属配信（全時間足を同一設計にするための単一権威の配信）。

固定する契約:
  - timeframe を申告すれば、tick と同数・同順の barTimes と nowBarTime が返る。
  - **時間足による差が無い**（1m..1M のどれでも同じ形の応答）。値は
    marketdata.tf_meta.bar_time_unix（ロールアップ足と同じラベル規約）と完全一致する。
  - 未知 timeframe / 未申告は None（従来応答のまま＝後方互換）。
"""

from __future__ import annotations

import pytest

from adapter.controller.live_tick_bars_controller import handle_live_tick_bar_times
from marketdata.tf_meta import bar_time_unix

_NOW_MS = 1_785_794_400_000                      # 2026-08-03 22:00 UTC
_TICKS = [[_NOW_MS - 5_000, 100.0], [_NOW_MS - 1_000, 101.0]]

ALL_TF = ["1m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M"]


@pytest.mark.parametrize("tf", ALL_TF)
def test_bar_times_are_emitted_for_every_timeframe(tf):
    out = handle_live_tick_bar_times({"timeframe": [tf]}, _TICKS, _NOW_MS)
    assert out is not None
    assert out["barTimes"] == [bar_time_unix(tf, t[0] // 1000) for t in _TICKS]
    assert out["nowBarTime"] == bar_time_unix(tf, _NOW_MS // 1000)


@pytest.mark.parametrize("tf", ALL_TF)
def test_bar_times_align_one_to_one_with_ticks(tf):
    out = handle_live_tick_bar_times({"timeframe": [tf]}, _TICKS, _NOW_MS)
    assert len(out["barTimes"]) == len(_TICKS)


def test_empty_ticks_still_reports_the_current_bar():
    out = handle_live_tick_bar_times({"timeframe": ["1W"]}, [], _NOW_MS)
    assert out["barTimes"] == [] and out["nowBarTime"] == bar_time_unix("1W", _NOW_MS // 1000)


@pytest.mark.parametrize("query", [{}, {"timeframe": ["unknown"]}, {"timeframe": [""]}])
def test_unknown_or_missing_timeframe_returns_none(query):
    assert handle_live_tick_bar_times(query, _TICKS, _NOW_MS) is None
