"""augment_forming_payload の移設回帰（ISSUE-094 🟡-8）。

旧 indicator_ui/framework/server.py の ``_augment_mp_forming_ticks``（MP forming payload への
buffer tick 合成＝業務判断）を MP 側 controller の ``augment_forming_payload`` へ移設した
（殻は buffer を引数で渡すだけ）。純関数 ``augment_forming_ticks`` は MP compute へ移設し
indicator_ui/adapter/compute/forming_bar は再エクスポートで温存する。本テストは移設後の
挙動（合成・no-op 条件・since 適用）が移設前と同値であることを固定する。
"""
from __future__ import annotations

from market_profile_api.compute import market_profile_forming as mpf
from market_profile_api.controller.market_profile_forming_controller import (
    augment_forming_payload,
)


class _FakeBuffer:
    """LiveTickBuffer 互換の最小フェイク。``ticks_since(ms)`` は ms 超（境界含む起点）の (ms, mid)。"""

    def __init__(self, ticks_ms):
        self._ticks = list(ticks_ms)  # [(unix_ms, mid), ...]

    def ticks_since(self, since_ms):
        return [t for t in self._ticks if t[0] > since_ms]


def _payload(forming_start, now, parquet_ticks):
    return {
        "ok": True,
        "formingStart": forming_start,
        "now": now,
        "ticks": list(parquet_ticks),
    }


def test_buffer_fills_gap_after_parquet_frontier_inplace() -> None:
    # parquet は [1000, 1010] まで。buffer が 1040s/1080s を持つ → 末尾を秒重複なく補完（in-place）。
    payload = _payload(1000, 1100, [[1000, 5.0], [1010, 5.1]])
    buffer = _FakeBuffer([(1040 * 1000, 6.0), (1080 * 1000, 6.2)])
    augment_forming_payload(payload, "jp225_tick", "1m", None, buffer=buffer)
    assert payload["ticks"] == [[1000, 5.0], [1010, 5.1], [1040, 6.0], [1080, 6.2]]


def test_since_filter_applied_to_combined() -> None:
    payload = _payload(1000, 1100, [[1000, 5.0], [1010, 5.1]])
    buffer = _FakeBuffer([(1040 * 1000, 6.0), (1080 * 1000, 6.2)])
    augment_forming_payload(payload, "jp225_tick", "1m", "1020", buffer=buffer)
    assert payload["ticks"] == [[1040, 6.0], [1080, 6.2]]


def test_noop_when_buffer_none() -> None:
    payload = _payload(1000, 1100, [[1000, 5.0]])
    augment_forming_payload(payload, "jp225_tick", "1m", None, buffer=None)
    assert payload["ticks"] == [[1000, 5.0]]


def test_noop_for_non_tick_ref() -> None:
    payload = _payload(1000, 1100, [[1000, 5.0]])
    buffer = _FakeBuffer([(1040 * 1000, 6.0)])
    augment_forming_payload(payload, "sample", "1m", None, buffer=buffer)
    assert payload["ticks"] == [[1000, 5.0]]


def test_noop_for_unsupported_timeframe() -> None:
    payload = _payload(1000, 1100, [[1000, 5.0]])
    buffer = _FakeBuffer([(1040 * 1000, 6.0)])
    augment_forming_payload(payload, "jp225_tick", "1W", None, buffer=buffer)
    assert payload["ticks"] == [[1000, 5.0]]


def test_noop_when_payload_missing_ticks() -> None:
    payload = {"ok": True, "formingStart": 1000, "now": 1100}
    buffer = _FakeBuffer([(1040 * 1000, 6.0)])
    augment_forming_payload(payload, "jp225_tick", "1m", None, buffer=buffer)
    assert "ticks" not in payload


def test_pure_augment_forming_ticks_moved_to_mp_compute() -> None:
    # 純関数の実体が MP compute にあり、parquet 空でも buffer から窓内 tick を組む。
    out = mpf.augment_forming_ticks([], [(1040 * 1000, 6.0), (1080 * 1000, 6.2)], 1000, 1100)
    assert out == [[1040, 6.0], [1080, 6.2]]
