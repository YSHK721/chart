"""UC: scan_contacts — 価格×指標の接点（クロス）抽出の usecase 結線（estimate_weekly_band 踏襲）。

純エンジン（usecase/contact_scan）へ委譲し、events / summary を束ねて返す。tick 源は
``ticks_fn``（DI 注入）で受け、usecase は tick 源を知らない。MA は ``ma_values``（bar_index →
MA 値）で受け、bar_times により ma_by_time へ写像する。numpy/pandas は import しない（純 stdlib）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from simulator.usecase.contact_scan.engine import (
    ScanConfig,
    build_context,
    make_summary,
    scan,
)
from simulator.usecase.contact_scan.spec import MovingAverageContact

if TYPE_CHECKING:
    from simulator.usecase.scan_contacts_ports import TickWindowSource


@dataclass
class ScanContactsRequest:
    """接点スキャンの入力。OHLC は plain 配列（bar_times と位置対応・昇順）。"""
    ref: str
    timeframe: str
    indicator: str
    variant: str
    params: dict
    bar_times: "Sequence[int]"
    highs: "Sequence[float]"
    lows: "Sequence[float]"
    closes: "Sequence[float]"
    full_scan: bool = True


@dataclass
class ScanContactsResult:
    events: "list[dict]" = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def scan_contacts(
    *,
    request: ScanContactsRequest,
    ticks_fn: "TickWindowSource",
    ma_values: "dict[int, float]",
) -> ScanContactsResult:
    """接点イベントと summary を算出する。

    ticks_fn(start, end) -> [(sec, mid), ...]（full_scan 時のみ呼ばれる）。
    ma_values: bar_index → MA 値（前足 MA 参照は spec が bar_time 経由で行う）。
    """
    # ScanContext の構築は engine.build_context に一元化する（DRY）。ma_values(bar_index→値) は
    #   bar_time→値 の ma_series 形へ写して渡す（build_context が ma_by_time へ写像・float/int 化）。
    bt = [int(t) for t in request.bar_times]
    ma_series = [{"time": bt[int(idx)], "value": float(val)}
                 for idx, val in ma_values.items() if 0 <= int(idx) < len(bt)]
    ctx = build_context(
        bar_times=request.bar_times,
        highs=request.highs,
        lows=request.lows,
        closes=request.closes,
        ma_series=ma_series,
    )
    cfg = ScanConfig(
        ref=request.ref,
        timeframe=request.timeframe,
        indicator=request.indicator,
        variant=request.variant,
        params=request.params,
        full_scan=request.full_scan,
    )
    spec = MovingAverageContact()
    counts: dict = {}
    events = list(scan(ctx, spec, cfg, summary=counts, ticks_fn=ticks_fn))
    summary = make_summary(cfg, ctx, counts)
    return ScanContactsResult(events=events, summary=summary)
