"""Composition Root の real_ticks 結線テスト（every-tick #6・main 結線）。

config.tick_model == "real_ticks" のとき、main が ParquetTickRepository を
TickDataPort として対象期間の tick を load_ticks し RealTickModel に供給して
Interactor を構築することを固定する。実 marketdata/ticks/*.parquet（gitignore・
大容量）は読まず、tmp_path に小さな日別 parquet を書いて検証する。

既定経路（every_tick/ohlc_expand/open_only）の build は不変であることも固定する。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.main import build_interactor


# 合成 OHLC（end-to-end テストと同型・MADiff SMA period=2 が bar2 で買いクロス）
_ROWS = [
    ("2024-01-01T00:00:00", 1.1000, 1.1010, 1.0990, 1.0995, 1.0, 0),
    ("2024-01-01T00:01:00", 1.1000, 1.1010, 1.0985, 1.0990, 1.0, 0),
    ("2024-01-01T00:02:00", 1.0990, 1.1050, 1.0990, 1.1040, 1.0, 0),
    ("2024-01-01T00:03:00", 1.1040, 1.1100, 1.1040, 1.1090, 1.0, 0),
    ("2024-01-01T00:04:00", 1.1090, 1.1120, 1.0900, 1.0950, 1.0, 0),
    ("2024-01-01T00:05:00", 1.0950, 1.0960, 1.0900, 1.0920, 1.0, 0),
]


def _write_csv(path: Path) -> Path:
    lines = ["time,open,high,low,close,volume,spread"]
    for t, o, h, l, c, v, s in _ROWS:
        lines.append(f"{t},{o},{h},{l},{c},{v},{s}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_tick_store(root: Path, symbol: str = "EURUSD") -> Path:
    """tmp_path に小さな日別 parquet（hive layout）を書く（実データは読まない）。"""
    part = (
        root / symbol / "year=2024" / "month=01" / "day=01" / "part.parquet"
    )
    part.parent.mkdir(parents=True, exist_ok=True)
    # 各 M1 バー区間内に 1 ティック（last=close 相当）。timestamp 昇順。
    rows = [
        ("2024-01-01T00:00:30", 1.0994, 1.0996, 1.0995, 1.0),
        ("2024-01-01T00:01:30", 1.0989, 1.0991, 1.0990, 1.0),
        ("2024-01-01T00:02:30", 1.1039, 1.1041, 1.1040, 1.0),
        ("2024-01-01T00:03:30", 1.1089, 1.1091, 1.1090, 1.0),
        ("2024-01-01T00:04:30", 1.0949, 1.0951, 1.0950, 1.0),
        ("2024-01-01T00:05:30", 1.0919, 1.0921, 1.0920, 1.0),
    ]
    df = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(t),
                "bid": bid,
                "ask": ask,
                "last": last,
                "volume": vol,
            }
            for t, bid, ask, last, vol in rows
        ]
    )
    df.to_parquet(part, index=False)
    return root


def _write_distinct_tick_store(root: Path, symbol: str = "EURUSD") -> Path:
    """各 M1 バー区間内に 1 ティック。bar2 ask と bar4 bid を bar OHLC と判別可能な固有値に。

    end-to-end の「約定が tick 価格に一致するか（bar 価格でないか）」を値で固定するため、
    買い建てバー（bar2）の ask＝1.2222、reverse 決済バー（bar4・long 決済=bid）の bid＝
    1.3333 を、どの bar OHLC 値とも一致しない固有値にする。timestamp 昇順・各バー区間内。
    """
    part = (
        root / symbol / "year=2024" / "month=01" / "day=01" / "part.parquet"
    )
    part.parent.mkdir(parents=True, exist_ok=True)
    # (timestamp, bid, ask, last, volume)。bar2 ask=1.2222・bar4 bid=1.3333 が固有値。
    rows = [
        ("2024-01-01T00:00:30", 1.0994, 1.0996, 1.0995, 1.0),
        ("2024-01-01T00:01:30", 1.0989, 1.0991, 1.0990, 1.0),
        ("2024-01-01T00:02:30", 1.2220, 1.2222, 1.2221, 1.0),  # bar2 買い建て: ask=1.2222
        ("2024-01-01T00:03:30", 1.1089, 1.1091, 1.1090, 1.0),
        ("2024-01-01T00:04:30", 1.3333, 1.3335, 1.3334, 1.0),  # bar4 reverse 決済: bid=1.3333
        ("2024-01-01T00:05:30", 1.0919, 1.0921, 1.0920, 1.0),
    ]
    df = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(t),
                "bid": bid,
                "ask": ask,
                "last": last,
                "volume": vol,
            }
            for t, bid, ask, last, vol in rows
        ]
    )
    df.to_parquet(part, index=False)
    return root


def _meta(csv_path: Path, **overrides) -> dict:
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
    base.update(overrides)
    return base


class TestRealTicksWiring:
    def test_real_ticks_config_injects_real_tick_model(self, tmp_path):
        # Arrange: 小さな OHLC CSV + 小さな日別 parquet tick-store（tmp_path）。
        from backtest.adapter.execution.tick_model import RealTickModel

        csv_path = _write_csv(tmp_path / "synth_m1.csv")
        tick_root = _write_tick_store(tmp_path / "ticks")
        # Act: real_ticks 指定で build（tick_store_root を注入）。
        controller, request = build_interactor(
            **_meta(
                csv_path,
                config_overrides={"tick_model": "real_ticks"},
                tick_store_root=tick_root,
            )
        )
        # Assert: Interactor の tick_model が RealTickModel（実ティック供給経路）。
        assert isinstance(controller._interactor._tick_model, RealTickModel)

    def test_real_ticks_end_to_end_entry_exit_at_tick_prices(self, tmp_path):
        # Arrange: 小 OHLC CSV ＋ tick 価格を bar OHLC と判別可能にずらした tick-store。
        #   bar2（買い建て）の tick ask と bar4（reverse 決済=long 決済=bid）の tick bid を
        #   どの bar OHLC 値とも一致しない固有値にして「tick 価格で約定したか」を判別する。
        csv_path = _write_csv(tmp_path / "synth_m1.csv")
        tick_root = _write_distinct_tick_store(tmp_path / "ticks")

        # Act: real_ticks 指定で build → main 実経路（_bar_period→load_ticks→
        #   RealTickModel→_execute_every_tick）を controller._interactor.execute で走らせる。
        controller, request = build_interactor(
            **_meta(
                csv_path,
                config_overrides={"tick_model": "real_ticks"},
                tick_store_root=tick_root,
            )
        )
        result = controller._interactor.execute(request)

        # Assert: 確定トレード 1 件（bar2 買い → bar4 reverse 決済）。
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.side == "buy"
        assert trade.exit_reason == "reverse"
        # entry_price は bar2 の tick ask（1.2222）＝ tick 価格であり bar の値ではない。
        assert trade.entry_price == pytest.approx(1.2222)
        assert trade.entry_price != pytest.approx(_ROWS[2][1])  # bar2.open
        assert trade.entry_price != pytest.approx(_ROWS[2][4])  # bar2.close
        # exit_price は bar4 の tick bid（1.3333・long 決済=bid）＝ tick 価格。
        assert trade.exit_price == pytest.approx(1.3333)
        assert trade.exit_price != pytest.approx(_ROWS[4][4])  # bar4.close
        assert trade.exit_time == request.bars[4].time

    def test_default_tick_model_build_unchanged(self, tmp_path):
        # Arrange / Act: 既定（ohlc_expand）は従来どおり OhlcExpandTickModel。
        from backtest.adapter.execution.tick_model import (
            OhlcExpandTickModel,
            RealTickModel,
        )

        csv_path = _write_csv(tmp_path / "synth_m1.csv")
        controller, request = build_interactor(**_meta(csv_path))
        # Assert: 既定経路は RealTickModel ではない（build 不変）。
        assert not isinstance(controller._interactor._tick_model, RealTickModel)
        assert isinstance(controller._interactor._tick_model, OhlcExpandTickModel)
