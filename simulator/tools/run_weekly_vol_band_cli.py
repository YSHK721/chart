"""週次ボラ・バンド戦略 CLI / 合成器（tools=Composition Root・詳細設計 §4.3・D1）。

make_segment_runner: forecast を週セグメントへ注入し、WeeklyVolBand＋実
RunBacktestInteractor を週セグメント bars で 1 回回す run_segment コールバックを返す。
engine の exit_reason→exit_type 写像（sl→stop / tp→tp / end_of_test→timeout）で
WeeklyLogRecord を構築する。

tools は pandas/IO 許容（DI-4）。usecase（run_weekly_segments）へは run_segment を注入。
"""
from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

from simulator.adapter.strategy.weekly_vol_band import WeeklyVolBand
from simulator.domain.variance_forecast import VarianceForecast
from simulator.domain.volatility_band import VolatilityBand
from simulator.usecase.models import BacktestConfig, SymbolSpec
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest
from simulator.usecase.run_weekly_segments import WeeklyLogRecord, WeeklySegmentOutcome

# exit_reason → exit_type 写像（詳細設計 §7.1・仕様 §2.5 {利確,ストップ,時間切れ}）。
_EXIT_MAP = {"sl": "stop", "tp": "tp", "end_of_test": "timeout", "stop_out": "stop"}


class _OpenIndicators:
    """セグメント bars の open を "open" 指標として供給する registry（IndicatorPort 互換）。"""

    def __init__(self, bars: "Sequence[Any]") -> None:
        self._series = {"open": pd.Series([b.open for b in bars])}

    def get(self, name: str) -> Any:
        return self._series[name]

    def update(self, bar_index: int) -> None:
        pass


class _OhlcTickModel:
    """bar-mode 用に O→H→L→C の 4 擬似ティックを供給する TickModel。"""

    def ticks_of(self, bar: Any, prev_close: float):
        return [
            (bar.open, bar.open, bar.open, bar.time),
            (bar.high, bar.high, bar.high, bar.time),
            (bar.low, bar.low, bar.low, bar.time),
            (bar.close, bar.close, bar.close, bar.time),
        ]


def _segment_config(digits: int) -> BacktestConfig:
    return BacktestConfig(
        tick_model="real_ticks", spread_model="fixed", sltp_tie="sl",
        fill_delay="next_tick", ohlc_order="auto", session_calendar="none",
        digits=digits, legacy_quirks=False, return_basis="equity",
        entry_price_basis="current_open", pending_lifecycle=True,
    )


def _segment_spec(digits: int) -> SymbolSpec:
    return SymbolSpec(
        contract_size=1.0, volume_min=0.0, volume_max=1e12, volume_step=0.0,
        stops_level=0, digits=digits, point_size=10.0 ** (-digits), leverage=10.0,
    )


def make_segment_runner(*, p_tp: float, capital: float, f_risk: float = 0.01, digits: int = 2):
    """run_weekly_segments へ注入する run_segment コールバックを生成する（D1 配線）。"""

    def _run_segment(week_bars: "Sequence[Any]", week_id: str, fc: VarianceForecast) -> WeeklySegmentOutcome:
        bars = list(week_bars)
        strat = WeeklyVolBand(forecast=fc, p_tp=p_tp, capital=capital, f_risk=f_risk)
        strat.on_init({"digits": digits, "volume_step": 0.0}, None)
        interactor = RunBacktestInteractor(
            strategy=strat, indicators=_OpenIndicators(bars), tick_model=_OhlcTickModel(),
        )
        req = RunBacktestRequest(
            config=_segment_config(digits), bars=bars, symbol_spec=_segment_spec(digits),
            initial_deposit=capital, stop_out_level=0.0,
        )
        res = interactor.execute(req)
        O = float(bars[0].open) if bars else 0.0
        band = VolatilityBand.from_forecast(
            week_id=week_id, O=O, sigma_minus=fc.sigma_minus, sigma_plus=fc.sigma_plus,
            p_tp=p_tp, f_risk=f_risk, capital=capital,
        )
        if res.trades:
            tr = res.trades[0]
            exit_type = _EXIT_MAP.get(tr.exit_reason, "none")
            gross = tr.pnl()
            holding_days = (int(tr.exit_time) - int(tr.entry_time)) / 86_400.0 if isinstance(tr.exit_time, int) else 0.0
            net = gross  # コストは UC 後付け（既定 0.0・詳細設計 §7.2）
            log = WeeklyLogRecord(
                week_id=week_id, O=O, sigma_plus=fc.sigma_plus, sigma_minus=fc.sigma_minus,
                S=band.S, T=band.T, N=band.N, entry_flag=True, exit_type=exit_type,
                holding_days=holding_days, gross_pnl=gross, cost=0.0, net_pnl=net,
                event_flag=False,
            )
        else:
            log = WeeklyLogRecord(
                week_id=week_id, O=O, sigma_plus=fc.sigma_plus, sigma_minus=fc.sigma_minus,
                S=band.S, T=band.T, N=band.N, entry_flag=False, exit_type="none",
                holding_days=0.0, gross_pnl=0.0, cost=0.0, net_pnl=0.0, event_flag=False,
            )
        return WeeklySegmentOutcome(week_id=week_id, log=log, stats=res)

    return _run_segment
