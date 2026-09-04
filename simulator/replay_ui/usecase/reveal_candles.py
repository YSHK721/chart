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
    """/candles の入力。

    ``start``/``pre``（任意・既定 None/0）: リプレイバーのカレンダーで選んだ再生開始日を起点に
    窓を取るときのみ使う。``start=None`` は従来どおり末尾 ``limit`` 本（tail）＝挙動不変。
    """
    ref: str
    timeframe: "str | None"
    limit: "int | None"
    start: "int | None" = None
    pre: int = 0


def reveal_candles(
    *, request: RevealCandlesRequest, candle_port: "CausalCandlePort"
) -> "list[dict]":
    """足列 ``[{time,open,high,low,close}]`` を返す（Port へ忠実委譲）。

    ``start`` 指定時のみ ``WindowedCandlePort.load_candles_from``（開始時刻起点）へ委譲する。
    未指定時の経路・引数は従来と 1 つも変えない（既存 Port 実装／fake は無改変で成立）。
    """
    if request.start is None:
        return candle_port.load_candles(request.ref, request.timeframe, request.limit)
    return candle_port.load_candles_from(
        request.ref, request.timeframe, request.start, request.pre, request.limit
    )
