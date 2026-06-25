"""Composition Root の marketdata 委譲分岐の結線回帰テスト（S5・§10.1 C-2・§10.2 H-4）。

設計正典: MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md §3.3（composition での DI）/ §6 S5 行 /
§10.1 C-2（source_ref=(start,end) を MarketDataSourceRepository へ・registry 用 DataFrame は
data_path から）/ §10.2 H-4（委譲は comma 形式戦略=既定 TC・WeeklyVolBand に限定。spread 依存
戦略 MA_Slope/MA_Slope_Pending/StopEntryProbe は委譲対象外＝Mt5CsvOHLCRepository 維持）。

検証対象:
  1. marketdata_window=(start,end) を渡すと comma 形式戦略（既定 TC・WeeklyVolBand）の
     market_data が MarketDataSourceRepository（委譲経路）になる。
  2. registry 用 DataFrame は従来どおり data_path から構築される（併存・U6 解決）。
  3. usecase IF（RunBacktestRequest.bars）は不変＝bars が Bar 列として構築される。

回帰観点（memory bugfix-pair-with-regression-test）:
  - spread 依存戦略（StopEntryProbe_EA）は marketdata_window を渡しても委譲経路に紛れ込まず
    Mt5CsvOHLCRepository を維持する（report.json 再現性＝StopEntryProbe 経路無改変）。
  - marketdata_window 未指定（None）なら既定経路は従来どおり CsvOHLCRepository（後方互換）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from simulator.adapter.repository.marketdata_source import MarketDataSourceRepository
from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe
from simulator.adapter.strategy.tc24051901 import TC24051901
from simulator.main import build_interactor


def _epoch(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def _write_comma_csv(path: Path) -> Path:
    """time が UNIX 秒 int の comma 形式 CSV（委譲経路の Candle 写像に適合）。"""
    t0 = _epoch(2024, 1, 1)
    rows = [
        "time,open,high,low,close,volume,spread",
        f"{t0 + 0},1.10,1.101,1.099,1.0995,1.0,0",
        f"{t0 + 60},1.10,1.101,1.0985,1.099,1.0,0",
        f"{t0 + 120},1.099,1.105,1.099,1.104,1.0,0",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _write_mt5_csv(path: Path) -> Path:
    """StopEntryProbe_EA 用 MT5 タブ形式 CSV（spread 依存経路）。"""
    rows = [
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>",
        "2024.01.01\t00:00:00\t100.0\t100.5\t99.5\t100.2\t10\t0\t2",
        "2024.01.01\t00:01:00\t100.2\t100.8\t100.0\t100.6\t11\t0\t2",
        "2024.01.01\t00:02:00\t100.6\t101.0\t100.4\t100.9\t12\t0\t2",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _tc_kwargs(csv_path: Path, **extra) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="EURUSD",
        period="M1",
        ea_name="TC24051901",
        initial_deposit=10_000.0,
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=0.0001,
        leverage=100.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=500,
        take_profit_points=3000,
        config_overrides={"tick_model": "ohlc_expand"},
    )
    base.update(extra)
    return base


def _stop_probe_kwargs(csv_path: Path, **extra) -> dict:
    base = dict(
        data_path=csv_path,
        symbol="JP225",
        period="M1",
        ea_name="StopEntryProbe_EA",
        initial_deposit=1_000_000.0,
        contract_size=1.0,
        volume_min=0.0,
        volume_max=1_000_000.0,
        volume_step=0.0,
        stops_level=0,
        digits=2,
        point_size=0.01,
        leverage=10.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=0,
        take_profit_points=0,
        config_overrides={"tick_model": "ohlc_expand"},
    )
    base.update(extra)
    return base


_WINDOW = (
    datetime(2024, 1, 1, tzinfo=timezone.utc),
    datetime(2024, 1, 2, tzinfo=timezone.utc),
)


def test_tc_with_marketdata_window_uses_delegation_repository(tmp_path):
    # Arrange: comma 形式 + marketdata_window
    csv = _write_comma_csv(tmp_path / "tc.csv")
    # Act
    controller, _ = build_interactor(**_tc_kwargs(csv, marketdata_window=_WINDOW))
    # Assert: 委譲経路（MarketDataSourceRepository）に結線される（C-2）
    assert isinstance(controller._market_data, MarketDataSourceRepository)
    # registry/戦略は不変（TC24051901）
    assert isinstance(controller._interactor._strategy, TC24051901)


def test_tc_without_window_keeps_csv_ohlc_repository(tmp_path):
    # Arrange: marketdata_window 未指定（None）
    csv = _write_comma_csv(tmp_path / "tc.csv")
    # Act
    controller, _ = build_interactor(**_tc_kwargs(csv))
    # Assert: 後方互換＝従来 CsvOHLCRepository（委譲経路に入らない）
    assert isinstance(controller._market_data, CsvOHLCRepository)


def test_delegation_request_bars_are_bar_list(tmp_path):
    # Arrange: usecase IF（RunBacktestRequest.bars）不変の確認
    csv = _write_comma_csv(tmp_path / "tc.csv")
    # Act
    _, request = build_interactor(**_tc_kwargs(csv, marketdata_window=_WINDOW))
    # Assert: bars が Bar 列として構築される（委譲経路でも IF 不変）
    from simulator.domain.bar import Bar

    assert isinstance(request.bars, list)
    assert len(request.bars) == 3
    assert all(isinstance(b, Bar) for b in request.bars)


def test_stop_entry_probe_never_uses_delegation_even_with_window(tmp_path):
    # Arrange: spread 依存戦略 + marketdata_window（紛れ込み禁止の回帰の壁・H-4）
    csv = _write_mt5_csv(tmp_path / "probe.csv")
    # Act: window を渡しても委譲経路に入ってはならない
    controller, _ = build_interactor(**_stop_probe_kwargs(csv, marketdata_window=_WINDOW))
    # Assert: report.json 由来の StopEntryProbe 経路は Mt5CsvOHLCRepository を維持
    assert isinstance(controller._market_data, Mt5CsvOHLCRepository)
    assert not isinstance(controller._market_data, MarketDataSourceRepository)
    assert isinstance(controller._interactor._strategy, StopEntryProbe)
