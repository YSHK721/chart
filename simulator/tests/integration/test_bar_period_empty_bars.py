"""空のバー列から実ティック読込区間を導けないことの表明（ISSUE-400）。

固定する事実:
    R-1: 取得窓が bars を 0 本に絞り、かつ `tick_model=real_ticks` で
         `tick_start`/`tick_end` が未指定のとき、`build_interactor` は
         **翻訳される例外**（`BacktestError` 系＝`exit_code_for` の表に載る）を
         送出する。是正前は `_bar_period` の `list index out of range`
         （`IndexError`）が翻訳されずに呼出側へ漏れていた。
    R-2: A-3（窓を全 Repository へ効かせる）以前から存在する comma 委譲経路
         （`CsvOHLCRepository` → `MarketDataSourceRepository`）でも、A-3 が到達
         可能にした MT5 経路（`WindowedMarketDataRepository`）でも、同一の失敗に
         なる＝A-3 は到達範囲を広げただけである。
    R-3: バー系列を消費しない modelling（`math_calculations`・
         `requires_market_data is False`）は bars=[] が**正常状態**であり、
         本是正で例外にならない（`_bar_period` へは到達しない）。
    R-4: 失敗は `exit_code_for` で終了コード 1（`BacktestError`）へ翻訳される。
    R-5: `tick_start`/`tick_end` を明示した実行は空のバー列でも例外にならない
         （空列そのものを一律に拒否していない＝導出を要求されたときだけ止まる）。

測り方: 実 CSV・実 `build_interactor`・実 tick-store（tmp_path）を通す。
`_bar_period` を直接叩く単体では「実経路で到達するか」を測れないため、到達経路
ごと固定する。

comma 委譲経路の `time` 列（実測）:
    `CsvCandleSource.fetch_candles` は `time` が UNIX 秒 int であることを契約とし、
    ISO 文字列は `ValueError`→`DataError` で fail-fast する（`marketdata/csv_source.py`）。
    したがって「窓が 0 本に絞る」状況を委譲経路で作るには epoch 整数の CSV が要る。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from simulator.adapter.exit_codes import exit_code_for
from simulator.adapter.repository.windowed_market_data import WindowedMarketDataRepository
from simulator.domain.exceptions import BacktestError, DataError
from simulator.main import build_interactor

#: 2024-01-01T00:00:00Z。comma 形式 CSV の `time` は UNIX 秒 int（Candle 契約 §2.1）。
_EPOCH_2024_01_01 = 1_704_067_200

#: どのバーも含まない窓（2030 年）。窓は半開 `[start, end)`。
_EMPTY_WINDOW = (
    datetime(2030, 1, 1, tzinfo=timezone.utc),
    datetime(2030, 1, 2, tzinfo=timezone.utc),
)


def _write_comma_csv(path: Path) -> Path:
    """comma 形式・epoch 整数 time の M1 3 本（委譲経路が読める形）。"""
    lines = ["time,open,high,low,close,volume,spread"]
    for i in range(3):
        lines.append(f"{_EPOCH_2024_01_01 + 60 * i},1.1000,1.1010,1.0990,1.0995,1.0,0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_mt5_csv(path: Path) -> Path:
    """MT5 タブ形式・1 日 1 本 5 本（`Mt5CsvOHLCRepository` 経路＝A-3 の合成デコレータ）。"""
    rows = ["<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"]
    for day in range(1, 6):
        rows.append(
            f"2024.01.0{day}\t00:00:00\t100.0\t100.5\t99.5\t100.2\t10\t0\t{10 * day}"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _write_tick_store(root: Path, symbol: str) -> Path:
    part = root / symbol / "year=2024" / "month=01" / "day=01" / "part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2024-01-01T00:00:30"),
                "bid": 1.0994,
                "ask": 1.0996,
                "last": 1.0995,
                "volume": 1.0,
            }
        ]
    ).to_parquet(part, index=False)
    return root


def _meta(csv_path, **overrides) -> dict:
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
    )
    base.update(overrides)
    return base


class TestEmptyBarsOnTheRealTicksPath:
    def test_comma_delegation_path_raises_translated_error(self, tmp_path):
        """R-1 / R-2 / R-4: comma 委譲経路（A-3 以前から存在）で翻訳される例外になる。"""
        csv_path = _write_comma_csv(tmp_path / "m1_epoch.csv")
        tick_root = _write_tick_store(tmp_path / "ticks", "EURUSD")

        with pytest.raises(BacktestError) as excinfo:
            build_interactor(
                **_meta(
                    csv_path,
                    config_overrides={"tick_model": "real_ticks"},
                    tick_store_root=tick_root,
                    marketdata_window=_EMPTY_WINDOW,
                )
            )

        error = excinfo.value
        assert isinstance(error, DataError)
        assert not isinstance(error, IndexError)
        assert error.context["bar_count"] == 0
        assert exit_code_for(error) == 1

    def test_windowed_decorator_path_raises_translated_error(self, tmp_path):
        """R-2: A-3 が到達可能にした MT5 経路（合成デコレータ）でも同一の失敗になる。"""
        csv_path = _write_mt5_csv(tmp_path / "mt5_daily.csv")
        tick_root = _write_tick_store(tmp_path / "ticks", "JP225")

        with pytest.raises(DataError) as excinfo:
            controller, _request = build_interactor(
                **_meta(
                    csv_path,
                    symbol="JP225",
                    ea_name="MA_Slope_EA",
                    ma_period=2,
                    ma_method="ema",
                    config_overrides={"tick_model": "real_ticks"},
                    tick_store_root=tick_root,
                    marketdata_window=_EMPTY_WINDOW,
                )
            )
        assert excinfo.value.context["bar_count"] == 0

    def test_windowed_decorator_is_the_mechanism_on_the_mt5_path(self, tmp_path):
        """上のテストが測っているのが本当に A-3 の合成デコレータであることを固定する。

        窓が bars を空にしない条件（実データを含む窓）で組み、`market_data` の実体が
        `WindowedMarketDataRepository` であることを確認する（機構の取り違えを防ぐ）。
        """
        csv_path = _write_mt5_csv(tmp_path / "mt5_daily.csv")
        controller, _request = build_interactor(
            **_meta(
                csv_path,
                symbol="JP225",
                ea_name="MA_Slope_EA",
                ma_period=2,
                ma_method="ema",
                marketdata_window=(
                    datetime(2024, 1, 2, tzinfo=timezone.utc),
                    datetime(2024, 1, 4, tzinfo=timezone.utc),
                ),
            )
        )
        assert isinstance(controller.market_data, WindowedMarketDataRepository)

    def test_explicit_tick_period_is_not_derived(self, tmp_path):
        """R-5: `tick_start`/`tick_end` 明示時は導出しないため空列でも通る。"""
        csv_path = _write_comma_csv(tmp_path / "m1_epoch.csv")
        tick_root = _write_tick_store(tmp_path / "ticks", "EURUSD")

        _controller, request = build_interactor(
            **_meta(
                csv_path,
                config_overrides={"tick_model": "real_ticks"},
                tick_store_root=tick_root,
                marketdata_window=_EMPTY_WINDOW,
                tick_start=pd.Timestamp("2024-01-01T00:00:00"),
                tick_end=pd.Timestamp("2024-01-01T00:10:00"),
            )
        )
        assert list(request.bars) == []


class TestMathCalculationsUnaffected:
    def test_math_normal_path_still_builds_with_empty_bars(self):
        """R-3: バー系列を消費しない modelling は bars=[] が正常状態のまま。"""
        _controller, request = build_interactor(
            **_meta(
                None,
                config_overrides={"tick_model": "math_calculations"},
            )
        )
        assert list(request.bars) == []
