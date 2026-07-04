"""UC-R3 intrabar_window — /intraday の usecase 結線（proto do_intraday 忠実）。

m1 は常に取得する（失敗は m1_error へ翻訳し計算全体は落とさない）。ticks は
mode=='real_ticks' のときのみ取得（他モードは tick 読込スキップ＝軽量維持）。
IntrabarWindowPort のみに依存し、domain E-4（mid 算出）は adapter 側の load_ticks に閉じる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import IntrabarWindowPort


@dataclass
class IntrabarWindowRequest:
    """/intraday の入力。"""
    ref: str
    start: int
    end: int
    mode: str = "real_ticks"


@dataclass
class IntrabarWindowResult:
    """do_intraday の payload（ok/m1/ticks・任意の *_error）。"""
    ok: bool = True
    m1: list = field(default_factory=list)
    ticks: list = field(default_factory=list)
    m1_error: "str | None" = None
    ticks_error: "str | None" = None


def intrabar_window(
    *, request: IntrabarWindowRequest, window_port: "IntrabarWindowPort"
) -> IntrabarWindowResult:
    """足内データ（m1 OHLC 行 / 実ティック mid 列）を返す。"""
    result = IntrabarWindowResult(ok=True, m1=[], ticks=[])
    try:
        result.m1 = window_port.load_m1_rows(request.ref, request.start, request.end)
    except Exception as exc:  # noqa: BLE001 — proto 同様 m1_error へ翻訳（計算を落とさない）
        result.m1_error = str(exc)[:120]
    if request.mode != "real_ticks":
        return result  # 他モードは m1 のみで足りる＝tick 読込スキップ（軽量維持）
    try:
        result.ticks = [mid for _sec, mid in window_port.load_ticks(request.start, request.end)]
    except Exception as exc:  # noqa: BLE001 — proto 同様 ticks_error へ翻訳
        result.ticks_error = str(exc)[:120]
    return result
