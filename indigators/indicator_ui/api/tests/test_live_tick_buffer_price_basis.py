"""``LiveTickBuffer`` が ref の価格基準で畳む（ISSUE-515 対策 2）。

用語: 価格基準＝ティックの bid/ask のどちらを価格とするか（``"mid"`` / ``"bid"``・語彙は
marketdata/tick_m1.py）。バッファは従来 mid 固定だった。確定足が bid の ref（``jp225_mt5``）で
mid を配ると、ライブ再生のローソクが確定のたびに半スプレッド前後跳ねる（ISSUE-515 実測 +7.5）。

既定は mid のまま（既存 11 件の検定と Dukascopy の既存表示を 1 バイトも変えない）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from adapter.compute.live_tick_buffer import LiveTickBuffer

_ROWS = [(1_000_100, 39000.0, 39010.0), (1_000_200, 39002.0, 39012.0)]


def _polled(**kwargs) -> LiveTickBuffer:
    buf = LiveTickBuffer(fetch_fn=lambda cursor: _ROWS, time_fn=lambda: 1000.0, **kwargs)
    buf._poll_once()
    return buf


def test_a_bid_buffer_keeps_the_bid():
    """bid 基準のバッファは bid を保持する（確定足と同じ価格）。"""
    # Arrange / Act
    buf = _polled(price_basis="bid")

    # Assert
    assert buf.ticks_since(0) == [(1_000_100, 39000.0), (1_000_200, 39002.0)]


def test_the_default_buffer_still_keeps_the_mid():
    """既定は mid のまま（Dukascopy の既存表示を変えない）。"""
    # Arrange / Act
    buf = _polled()

    # Assert
    assert buf.ticks_since(0) == [(1_000_100, 39005.0), (1_000_200, 39007.0)]


def test_an_unknown_basis_is_refused_when_the_buffer_is_built():
    """未知の基準は構築時に拒否する（ポーリングの失敗としてバックオフへ化けさせない）。"""
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        LiveTickBuffer(fetch_fn=lambda cursor: [], price_basis="ask")
