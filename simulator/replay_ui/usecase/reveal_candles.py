"""UC-R1 reveal_candles — /candles の usecase 結線（CausalCandlePort へ委譲）。

proto /candles は untilTime 切断を行わない（リビールはフロントの描画範囲で表現）。本 UC は
ref/timeframe/limit を Port へ渡し、足列を素通しする薄い Interactor。domain のみ依存。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import CausalCandlePort


@dataclass
class RevealCandlesRequest:
    """/candles の入力。"""
    ref: str
    timeframe: "str | None"
    limit: "int | None"


def reveal_candles(
    *, request: RevealCandlesRequest, candle_port: "CausalCandlePort"
) -> "list[dict]":
    """足列 ``[{time,open,high,low,close}]`` を返す（Port へ忠実委譲）。"""
    return candle_port.load_candles(request.ref, request.timeframe, request.limit)
