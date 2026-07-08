"""UC-R2 causal_compute — /compute の usecase 結線（proto do_compute 忠実）。

手順（proto do_compute と同順）:
    1. CausalComputePort.load_source(ref, tf)   （未知 ref/tf は ValueError）
    2. RevealClock.truncate(bars, untilTime)     （再生のその時点まで＝因果不変）
    3. tail(limit)                               （limit>0 のとき末尾 limit 本）
    4. 空なら [] を返す
    5. mode=='latest' なら FormingBar.apply(forming) して latest 計算、それ以外は full 計算

domain（reveal_clock / forming_bar）のみ依存。numpy/pandas を import しない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from simulator.replay_ui.domain.forming_bar import apply as apply_forming
from simulator.replay_ui.domain.reveal_clock import truncate

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import CausalComputePort


@dataclass
class CausalComputeRequest:
    """/compute の入力（proto body 準拠）。"""
    indicator: str
    variant: str
    ref: str
    timeframe: "str | None"
    limit: "int | None"
    until_time: "int | None"
    mode: "str | None"
    forming: "dict | None"
    params: dict


def causal_compute(
    *, request: CausalComputeRequest, compute_port: "CausalComputePort"
) -> "list[dict]":
    """series（plain dict の list）を返す。空窓は ``[]``。"""
    bars = compute_port.load_source(request.ref, request.timeframe)
    bars = truncate(bars, request.until_time)
    limit = request.limit
    if isinstance(limit, int) and limit > 0:
        bars = bars[-limit:]
    if len(bars) == 0:
        return []
    if request.mode == "latest":
        bars = apply_forming(bars, request.forming)
        return compute_port.compute(
            request.indicator, request.variant, "latest", bars, request.params
        )
    return compute_port.compute(
        request.indicator, request.variant, "full", bars, request.params
    )
