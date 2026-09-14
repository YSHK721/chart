"""UC-R1 reveal_candles: fake CausalCandlePort 注入の AAA（/candles = 忠実委譲）。

proto /candles は untilTime 切断を行わない（リビールはフロント）。UC は Port へ委譲する。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.reveal_candles import (
    RevealCandlesRequest,
    reveal_candles,
)


class _FakeCandlePort:
    def __init__(self, candles):
        self._candles = candles
        self.calls = []

    def load_candles(self, ref, timeframe, limit):
        self.calls.append((ref, timeframe, limit))
        return self._candles


def test_delegates_to_port_and_returns_candles():
    # Arrange
    candles = [{"time": 0, "open": 1, "high": 2, "low": 0, "close": 1.5}]
    port = _FakeCandlePort(candles)
    req = RevealCandlesRequest(ref="jp225_tick", timeframe="1D", limit=100)
    # Act
    out = reveal_candles(request=req, candle_port=port)
    # Assert — 引数がそのまま Port へ渡り、結果を素通しする（切断しない）。
    assert out == candles
    assert port.calls == [("jp225_tick", "1D", 100)]
