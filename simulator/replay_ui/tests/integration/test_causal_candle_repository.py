"""CausalCandleRepository の結線テスト（proto load_tick_candles / _repair_day_outliers 忠実）。

合成 jp225_tick_m1.csv を tmp に置き、外れバー除去 + 1D resample + tail + candle 形を検証する。
"""
from __future__ import annotations

import pandas as pd
import pytest

from simulator.replay_ui.adapter.causal_candle_repository import (
    CausalCandleRepository,
    _repair_day_outliers,
)


def _write_csv(path):
    rows = [
        # 2020-01-01: 正常 3 本 + 外れ 1 本（close=30 ≒ 日内中央値100 から -70% → 除去）
        ("2020-01-01 00:00:00", 100.0, 105.0, 99.0, 101.0, 1.0),
        ("2020-01-01 00:01:00", 101.0, 106.0, 100.0, 102.0, 1.0),
        ("2020-01-01 00:02:00", 30.0, 30.0, 30.0, 30.0, 1.0),  # 外れバー
        ("2020-01-01 00:03:00", 102.0, 108.0, 98.0, 104.0, 1.0),
        # 2020-01-02: 正常 2 本
        ("2020-01-02 00:00:00", 110.0, 112.0, 109.0, 111.0, 1.0),
        ("2020-01-02 00:01:00", 111.0, 115.0, 108.0, 113.0, 1.0),
    ]
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df.to_csv(path, index=False)


def test_repair_removes_outlier_bar():
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 30.0],
            "high": [105.0, 106.0, 30.0],
            "low": [99.0, 100.0, 30.0],
            "close": [101.0, 102.0, 30.0],
        },
        index=pd.to_datetime(["2020-01-01 00:00", "2020-01-01 00:01", "2020-01-01 00:02"]),
    )
    out = _repair_day_outliers(df, 0.3)
    assert len(out) == 2  # 外れバー 1 本除去
    assert 30.0 not in out["close"].tolist()


def test_load_tick_candles_1d_excludes_outlier_low(tmp_path):
    # Arrange
    csv = tmp_path / "jp225_tick_m1.csv"
    _write_csv(csv)
    repo = CausalCandleRepository(tick_m1_csv=csv)
    # Act
    candles = repo.load_candles("jp225_tick", "1D", None)
    # Assert — 2 日分 + 外れバー(low=30)が 1/1 の低値に混入しない。
    assert len(candles) == 2
    day1 = candles[0]
    assert day1["low"] == 98.0  # 外れバー除去後の最安（30 でない）
    assert day1["open"] == 100.0
    assert day1["close"] == 104.0


def test_limit_tail(tmp_path):
    csv = tmp_path / "jp225_tick_m1.csv"
    _write_csv(csv)
    repo = CausalCandleRepository(tick_m1_csv=csv)
    candles = repo.load_candles("jp225_tick", "1D", 1)
    assert len(candles) == 1
    assert candles[0]["open"] == 110.0  # 直近 1 本（2020-01-02）


def test_unknown_non_tick_ref_raises_valueerror(tmp_path):
    repo = CausalCandleRepository(tick_m1_csv=tmp_path / "nope.csv")
    with pytest.raises(ValueError):
        repo.load_candles("totally_unknown_ref", "1D", None)
