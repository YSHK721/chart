"""UC-WV2: 週単位セグメント orchestration（詳細設計 §4.3・D1）。

run_is_oos と同型の run_segment コールバック DIP。各週セグメント bars=その週の
first_trading_time〜last_trading_time。金曜引けは engine の end_of_test 清算が担う
（曜日判定で close する誤実装を禁止）。

usecase 層は domain と自層 Port のみ依存（numpy/pandas を import しない）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Sequence

from simulator.domain.trading_week import TradingWeek, week_id_of
from simulator.domain.variance_forecast import VarianceForecast

if TYPE_CHECKING:
    from simulator.domain.bar import Bar
    from simulator.usecase.vol_band_ports import VolBandReaderPort


class LookaheadViolationError(Exception):
    """look-ahead 違反（当週以降の入力が σ̂/S/T/N 算出に混入）。NFR-D4。"""


def assert_no_lookahead(target_week: str, input_week_ids: "Sequence[str]") -> None:
    """入力 week_id が全て target 週より厳密に手前であることを検証する（§6・NFR-D4）。"""
    for wid in input_week_ids:
        if wid >= target_week:
            raise LookaheadViolationError(
                f"look-ahead 違反: target={target_week} に対し入力 {wid}（当週以降）"
            )


SegmentRunner = Callable[["Sequence[Bar]", str, VarianceForecast], Any]


@dataclass
class WeeklyLogRecord:
    week_id: str
    O: float
    sigma_plus: "float | None"
    sigma_minus: "float | None"
    S: "float | None"
    T: "float | None"
    N: "float | None"
    entry_flag: bool
    exit_type: str
    holding_days: float
    gross_pnl: float
    cost: float
    net_pnl: float
    event_flag: bool


@dataclass
class WeeklySegmentOutcome:
    week_id: str
    log: WeeklyLogRecord
    stats: Any


@dataclass
class RunWeeklySegmentsRequest:
    full_bars: "Sequence[Bar]"
    e_rule: str
    p_tp: float
    capital: float
    f_risk: float = 0.01


def split_into_weeks(full_bars: "Sequence[Bar]") -> "list[TradingWeek]":
    """full_bars を week_id ごとに分割し TradingWeek 群を構築する純関数（決定論）。"""
    by_week: dict[str, list[int]] = {}
    for b in full_bars:
        wid = week_id_of(b.time)
        by_week.setdefault(wid, []).append(int(b.time))
    weeks: list[TradingWeek] = []
    for wid in sorted(by_week):
        times = tuple(sorted(by_week[wid]))
        weeks.append(TradingWeek(wid, times[0], times[-1], times))
    return weeks


def slice_week_bars(full_bars: "Sequence[Bar]", week: TradingWeek) -> "list[Bar]":
    """その週セグメントの bars（first<=time<=last かつ同一週）を返す純関数。"""
    return [
        b for b in full_bars
        if week.first_trading_time <= int(b.time) <= week.last_trading_time
        and week_id_of(b.time) == week.week_id
    ]


def _parse_e1_theta(e_rule: str) -> float:
    inner = e_rule[e_rule.index("(") + 1: e_rule.index(")")]
    return float(inner)


def entry_rule_true(
    e_rule: str,
    *,
    prev_week_close: Any,
    week: TradingWeek,
    forecast: VarianceForecast,
) -> bool:
    """エントリ規則 e の真偽（FR-WV-07・D2）。

    E0=無条件真。E1(θ)=前週 close-to-close リターン ≤ −θ·σ̂ᵗᵒᵗᵃˡ_w。前週確定値なし→False
    （look-ahead 安全側）。prev_week_close は (prev2_close, prev_close) タプル。
    """
    if e_rule == "E0":
        return True
    theta = _parse_e1_theta(e_rule)
    if prev_week_close is None or forecast.sigma_total_prev is None:
        return False
    prev2_close, prev_close = prev_week_close
    if prev2_close is None or prev_close is None or prev2_close <= 0 or prev_close <= 0:
        return False
    r_prev = math.log(prev_close / prev2_close)
    return r_prev <= -theta * forecast.sigma_total_prev


def _week_close(full_bars: "Sequence[Bar]", week: TradingWeek) -> float:
    last = week.last_trading_time
    for b in full_bars:
        if int(b.time) == last and week_id_of(b.time) == week.week_id:
            return float(b.close)
    return float("nan")


def _no_trade_record(week: TradingWeek) -> WeeklyLogRecord:
    return WeeklyLogRecord(
        week_id=week.week_id, O=0.0, sigma_plus=None, sigma_minus=None,
        S=None, T=None, N=None, entry_flag=False, exit_type="none",
        holding_days=0.0, gross_pnl=0.0, cost=0.0, net_pnl=0.0,
        event_flag=week.event_flag,
    )


def _no_trade_outcome(week: TradingWeek, reason: str) -> WeeklySegmentOutcome:
    return WeeklySegmentOutcome(week_id=week.week_id, log=_no_trade_record(week), stats=None)


def run_weekly_segments(
    *,
    request: RunWeeklySegmentsRequest,
    repo: "VolBandReaderPort",
    run_segment: SegmentRunner,
) -> "list[WeeklySegmentOutcome]":
    weeks = split_into_weeks(request.full_bars)
    prev2_close: float | None = None
    prev_close: float | None = None
    outcomes: list[WeeklySegmentOutcome] = []
    for wk in weeks:
        fc = repo.get(wk.week_id)
        this_close = _week_close(request.full_bars, wk)
        if fc is None or not fc.estimable:
            outcomes.append(_no_trade_outcome(wk, reason="not_estimable"))
            prev2_close, prev_close = prev_close, this_close
            continue
        if not entry_rule_true(
            request.e_rule, prev_week_close=(prev2_close, prev_close), week=wk, forecast=fc
        ):
            outcomes.append(_no_trade_outcome(wk, reason="entry_rule_false"))
            prev2_close, prev_close = prev_close, this_close
            continue
        week_bars = slice_week_bars(request.full_bars, wk)
        stats = run_segment(week_bars, wk.week_id, fc)
        outcomes.append(_outcome_from_stats(wk, fc, stats))
        prev2_close, prev_close = prev_close, this_close
    return outcomes


def _outcome_from_stats(
    week: TradingWeek, fc: VarianceForecast, stats: Any
) -> WeeklySegmentOutcome:
    """run_segment の戻り（WeeklySegmentOutcome or BacktestStats 等）を outcome に正規化。

    tools が build_interactor で渡す run_segment は WeeklySegmentOutcome を直接返す薄い
    ラッパを想定する（§4.3）。stats が WeeklySegmentOutcome ならそれを採用、None など
    最小ケースはエントリのみのログを構築する。
    """
    if isinstance(stats, WeeklySegmentOutcome):
        return stats
    log = WeeklyLogRecord(
        week_id=week.week_id, O=0.0,
        sigma_plus=fc.sigma_plus, sigma_minus=fc.sigma_minus,
        S=None, T=None, N=None, entry_flag=True, exit_type="none",
        holding_days=0.0, gross_pnl=0.0, cost=0.0, net_pnl=0.0,
        event_flag=week.event_flag,
    )
    return WeeklySegmentOutcome(week_id=week.week_id, log=log, stats=stats)
